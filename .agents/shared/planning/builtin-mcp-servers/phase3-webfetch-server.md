# Phase 3: Built-in Server — WebFetch

## Objective
Implement the `webfetch` built-in MCP server as the first concrete `BuiltinServerDefinition`. Uses `mcp-server-fetch` (Python, run via `uvx`) which provides a tool named `fetch` for retrieving web content. Implements `build_config` (values → MCP config) and `parse_config` (stored MCP config → values dict for form pre-fill).

## Coupling
- **Depends on**: Phase 1 (Backend Framework) — needs `BuiltinServerDefinition` ABC and registry
- **Coupling type**: loose
- **Shared files with other phases**: `daemon/mcp/builtin_servers/base.py` from Phase 1
- **Shared APIs/interfaces**: Implements `BuiltinServerDefinition`

## Context

### Technical Details
- **Package**: `mcp-server-fetch` (Python, PyPI)
- **Runner**: `uvx mcp-server-fetch` (isolated execution)
- **Tool name**: `fetch` (exposed to agents via MCP)
- **Transport**: stdio
- **Configuration**: CLI arguments only (no env vars)
  - `--user-agent=<string>` — Custom User-Agent header
  - `--ignore-robots-txt` — Boolean flag (presence = True)
  - `--proxy-url=<url>` — HTTP proxy URL
- **Prerequisites**: `uv`/`uvx` installed on system

---

## Tasks

### 1. Implement WebFetch Server Definition
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1.1 | Create webfetch module | `daemon/mcp/builtin_servers/webfetch.py` with `WebFetchBuiltinServer(BuiltinServerDefinition)` | same |
| 1.2 | Implement `name` | `"webfetch"` | same |
| 1.3 | Implement `description` | `"Fetch web page content — allows agents to read and extract content from URLs"` | same |
| 1.4 | Implement `schema_version` | `"1"` | same |
| 1.5 | Implement `get_base_config()` | `{ "transport": "stdio", "command": "uvx", "args": ["mcp-server-fetch"] }` | same |
| 1.6 | Implement `get_config_schema()` | Returns 3 fields (see §3) — all `section="args"`, no env | same |
| 1.7 | Inherit `build_config` | Generic implementation from `BuiltinServerDefinition` handles all 3 field types correctly; no override needed | same |
| 1.8 | Inherit `parse_config` | Generic implementation handles reverse-mapping for all 3 fields; no override needed | same |

### 2. Register WebFetch in Registry
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 2.1 | Import and register | In `daemon/mcp/builtin_servers/__init__.py`, import `WebFetchBuiltinServer`, call `registry.register(WebFetchBuiltinServer())` at module level | `daemon/mcp/builtin_servers/__init__.py` |

### 3. Configuration Schema Definition
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 3.1 | `user_agent` | `key="user_agent"`, `label="User Agent"`, `type="text"`, `section="args"`, `arg_format="key_value"`, `default="Mozilla/5.0 (compatible; MCP-WebFetch/1.0)"`, `required=False`, `description="Custom User-Agent string for HTTP requests"` | `daemon/mcp/builtin_servers/webfetch.py` |
| 3.2 | `ignore_robots_txt` | `key="ignore_robots_txt"`, `label="Ignore robots.txt"`, `type="boolean"`, `section="args"`, `arg_format="flag"`, `default=False`, `required=False`, `description="Bypass robots.txt restrictions when fetching pages"` | same |
| 3.3 | `proxy_url` | `key="proxy_url"`, `label="Proxy URL"`, `type="text"`, `section="args"`, `arg_format="key_value"`, `default=None`, `required=False`, `description="HTTP proxy URL for routing requests through a proxy server"` | same |

### 4. Config Generation & Reverse-Mapping Examples
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 4.1 | `build_config({})` — defaults | See §5.1 below | — |
| 4.2 | `build_config({ user_agent: "MyBot", ignore_robots_txt: true })` | See §5.2 below | — |
| 4.3 | `parse_config(config_from_5.2)` → `{ user_agent: "MyBot", ignore_robots_txt: true }` | Reverse-mapping extracts values from args | — |

### 5. Testing
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 5.1 | Test schema definition | `get_config_schema()` returns 3 fields with correct `arg_format` values | `tests/` |
| 5.2 | Test default config | `build_config({})` → includes `--user-agent` with default, omits `--ignore-robots-txt`, omits `--proxy-url` | `tests/` |
| 5.3 | Test flag True | `build_config({ ignore_robots_txt: True })` includes `"--ignore-robots-txt"` | `tests/` |
| 5.4 | Test flag False | `build_config({ ignore_robots_txt: False })` omits `"--ignore-robots-txt"` | `tests/` |
| 5.5 | Test key_value override | `build_config({ user_agent: "CustomBot/1.0" })` → `["--user-agent", "CustomBot/1.0"]` | `tests/` |
| 5.6 | Test None omission | `build_config({ proxy_url: None })` omits `--proxy-url` | `tests/` |
| 5.7 | Test parse_config roundtrip | `parse_config(build_config({ user_agent: "X", ignore_robots_txt: True }))` → `{ user_agent: "X", ignore_robots_txt: True }` | `tests/` |
| 5.8 | Test parse_config default config | `parse_config(build_config({}))` → `{ user_agent: "Mozilla/5.0 ...", ignore_robots_txt: false }` | `tests/` |
| 5.9 | Test parse_config with proxy | `parse_config(build_config({ proxy_url: "http://p:8080" }))` → includes `proxy_url: "http://p:8080"` | `tests/` |
| 5.10 | Integration test | WebFetch appears in `/builtin-templates`, can be configured via `/configure-builtin`, generates correct DB record | `tests/` |

---

## Key Files
- `daemon/mcp/builtin_servers/webfetch.py` — NEW: WebFetch server definition
- `daemon/mcp/builtin_servers/__init__.py` — UPDATED: Registration

## §5 — Generated Config Examples

### 5.1 Default config (`build_config({})`)
```json
{
  "transport": "stdio",
  "command": "uvx",
  "args": [
    "mcp-server-fetch",
    "--user-agent",
    "Mozilla/5.0 (compatible; MCP-WebFetch/1.0)"
  ]
}
```
`ignore_robots_txt` defaults to `false` → flag omitted. `proxy_url` defaults to `None` → omitted.

### 5.2 Custom user agent + ignore robots (`build_config({ user_agent: "MyBot/2.0", ignore_robots_txt: true })`)
```json
{
  "transport": "stdio",
  "command": "uvx",
  "args": [
    "mcp-server-fetch",
    "--user-agent",
    "MyBot/2.0",
    "--ignore-robots-txt"
  ]
}
```

### 5.3 All fields (`build_config({ user_agent: "MyBot/2.0", ignore_robots_txt: true, proxy_url: "http://proxy:8080" })`)
```json
{
  "transport": "stdio",
  "command": "uvx",
  "args": [
    "mcp-server-fetch",
    "--user-agent",
    "MyBot/2.0",
    "--ignore-robots-txt",
    "--proxy-url",
    "http://proxy:8080"
  ]
}
```

## §3 — Config Schema Reference

```python
[
    ConfigSchemaField(
        key="user_agent",
        label="User Agent",
        type="text",
        section="args",
        arg_format="key_value",
        description="Custom User-Agent string for HTTP requests",
        default="Mozilla/5.0 (compatible; MCP-WebFetch/1.0)",
        required=False,
    ),
    ConfigSchemaField(
        key="ignore_robots_txt",
        label="Ignore robots.txt",
        type="boolean",
        section="args",
        arg_format="flag",
        description="Bypass robots.txt restrictions when fetching pages",
        default=False,
        required=False,
    ),
    ConfigSchemaField(
        key="proxy_url",
        label="Proxy URL",
        type="text",
        section="args",
        arg_format="key_value",
        description="HTTP proxy URL for routing requests through a proxy server",
        default=None,
        required=False,
    ),
]
```

## Constraints
- **`uv`/`uvx` dependency**: System must have `uv` installed. Daemon logs clear error if unavailable.
- **Network access**: Server needs outbound HTTP access.
- **No env vars**: All config via CLI args; `section="env"` not used by this server.

## Deliverables
- [ ] `WebFetchBuiltinServer` class with `name`, `description`, `schema_version`, `get_base_config()`, `get_config_schema()`
- [ ] Inherits `build_config()` and `parse_config()` from base class (no overrides needed)
- [ ] 3 schema fields: user_agent (key_value), ignore_robots_txt (flag), proxy_url (key_value)
- [ ] Registration in registry
- [ ] 10 tests: schema, build_config scenarios, parse_config roundtrips, integration
