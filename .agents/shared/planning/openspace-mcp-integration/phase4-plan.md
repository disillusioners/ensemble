# Phase 4: Optional Utilities (Lower Priority)

## Objective

Port three standalone utility patterns from OpenSpace that improve robustness and developer experience but are not core to the MCP integration story: transport auto-detection, stdout safety wrapper for STDIO MCP servers, and MCP dependency auto-installer.

## Coupling

- **Depends on**: None
- **Coupling type**: independent — these are standalone utility modules with no shared dependencies on other phases
- **Shared files with other phases**: none
- **Shared APIs/interfaces**: none
- **Why this coupling**: Each utility is self-contained. They can be implemented independently and in any order.

## Context

These are quality-of-life improvements observed in OpenSpace's codebase. They address real edge cases in MCP server lifecycle management. Each can be deferred or implemented independently.

| Utility | OpenSpace Location | Our Need |
|---------|-------------------|----------|
| Transport auto-detection | `openspace/mcp_server.py:957-959` | Low — we primarily use stdio for builtins. Useful if we ever expose MCP servers externally. |
| Stdout safety wrapper | `openspace/mcp_server.py:30-80` | Medium — if we ever build our own MCP servers (like kb_server), `print()` calls could corrupt the JSON-RPC stream. |
| MCP dependency auto-install | Not in OpenSpace (new idea) | Medium — reduces friction for new builtin servers that need `uvx`/`npx` packages. |

## Tasks

### Task A: Transport Auto-Detection (Optional)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| A1 | Implement `detect_transport()` utility | Check `sys.stdin.isatty()` and `sys.stdout.isatty()`. If both are TTY → SSE transport. Otherwise → stdio. Add `hasattr` guard for test environments where stdin may be mocked. | `daemon/mcp/transport_utils.py` (NEW) |
| A2 | Integrate into MCP server creation | Allow builtin definitions to specify `"transport": "auto"` in `get_base_config()`. At warmup/connection time, resolve `"auto"` to actual transport via `detect_transport()`. | `daemon/mcp/builtin_servers/base.py`, `daemon/mcp/warmup_pool.py` |
| A3 | Tests | Test TTY detection, mock stdin/stdout, auto→stdio and auto→sSE resolution. | `tests/test_transport_detection.py` (NEW) |

### Task B: Stdout Safety Wrapper (Recommended for kb_server)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| B1 | Port `_MCPSafeStdout` class | Adapt OpenSpace's class: redirect text writes to stderr, keep binary `.buffer` on stdout. Properties: `buffer`, `fileno()`, `write()`, `writelines()`, `flush()`, `isatty()`, `encoding`, `errors`, `closed`, `readable()=False`, `writable()=True`, `seekable()=False`, `__getattr__` fallback to stderr. | `daemon/mcp/safe_stdout.py` (NEW) |
| B2 | Apply to kb_server MCP endpoint | Wrap `sys.stdout` in `_MCPSafeStdout` when kb_server is serving via stdio transport. This prevents accidental `print()` in tool implementations from corrupting the JSON-RPC protocol. | `daemon/mcp/kb_server.py` |
| B3 | Tests | Test that text writes go to stderr, binary buffer stays on stdout, all properties delegated correctly. | `tests/test_safe_stdout.py` (NEW) |

### Task C: MCP Dependency Auto-Installer (New)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| C1 | Implement dependency pre-check | Before registering a STDIO server in the warmup pool, check if the command (`uvx`, `npx`) is available via `shutil.which()`. If not, log a clear error with installation instructions. | `daemon/mcp/warmup_pool.py` |
| C2 | Add optional auto-install flag | When `MCP_AUTO_INSTALL_DEPS=true` (env var), attempt to install missing dependencies: `pip install uvx` or guide user to install Node.js for `npx`. This is best-effort — log warnings if installation fails. | `daemon/mcp/warmup_pool.py`, `daemon/mcp/dependency_check.py` (NEW) |
| C3 | Tests | Test dependency detection (present/absent), auto-install flag behavior. | `tests/test_mcp_dependency_check.py` (NEW) |

## Key Files

- `daemon/mcp/transport_utils.py` — **NEW**: `detect_transport()` utility
- `daemon/mcp/safe_stdout.py` — **NEW**: `_MCPSafeStdout` class
- `daemon/mcp/dependency_check.py` — **NEW**: Dependency detection and optional auto-install
- `daemon/mcp/warmup_pool.py` — Integration points for auto-detection and dependency check
- `daemon/mcp/kb_server.py` — Apply safe stdout wrapper
- `daemon/mcp/builtin_servers/base.py` — Support `"transport": "auto"` config value

## Constraints

- **All optional**: None of these utilities are required for Phases 1-3 to function
- **Non-breaking**: Each utility must be opt-in or have safe defaults
- **Transport auto-detection**: Only applies when `"transport": "auto"` is explicitly set. Default builtins keep their explicit transport.
- **Safe stdout**: Only wraps when serving as an MCP server (not in normal daemon operation)
- **Auto-install**: Disabled by default (`MCP_AUTO_INSTALL_DEPS` not set)

## Deliverables

- [ ] `detect_transport()` utility with TTY/pipe detection
- [ ] `_MCPSafeStdout` class ported and tested
- [ ] MCP dependency pre-check with optional auto-install
- [ ] Each utility has its own test file
