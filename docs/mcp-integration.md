# MCP Integration Guide

agents-ensemble supports the Model Context Protocol (MCP) for extending agent capabilities through external tool servers. MCP is Anthropic's open standard that enables AI assistants to connect to external tools and data sources.

agents-ensemble uses MCP in two distinct modes:

- **MCP Client Mode**: Agents consume tools from external MCP servers (webfetch, context7, custom servers)
- **MCP Server Mode**: agents-ensemble exposes its knowledge base tools to external AI agents via an embedded FastMCP server

---

## MCP Client Mode (Agents Consuming External Tools)

### Overview

In client mode, agents-ensemble connects to external MCP servers and makes their tools available to agent instances. Tools from MCP servers are automatically discovered when an instance spawns and injected into the agent's tool list.

### Built-in Servers

agents-ensemble ships with two pre-configured MCP servers:

#### WebFetch

Provides web content fetching capabilities using `mcp-server-fetch`.

| Property | Value |
|----------|-------|
| Name | `webfetch` |
| Transport | STDIO |
| Command | `uvx mcp-server-fetch` |
| Default Config | `{"user_agent": "Mozilla/5.0 (compatible; MCP-WebFetch/1.0)"}` |

**Configuration Options:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `user_agent` | text | Mozilla/5.0... | Custom User-Agent string |
| `ignore_robots_txt` | boolean | false | Bypass robots.txt restrictions |
| `proxy_url` | text | null | HTTP proxy URL (must start with http:// or https://) |

#### Context7

Provides up-to-date library documentation via `@upstash/context7-mcp`.

| Property | Value |
|----------|-------|
| Name | `context7` |
| Transport | STDIO |
| Command | `npx -y @upstash/context7-mcp` |
| Configuration | None required |

### Built-in Availability Pre-check

A built-in with external Python dependencies may not be installed in every deployment. The `BuiltinServerDefinition` base class supports a graceful-degradation pattern via the `is_available()` pre-check:

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

**When `is_available()` returns `False`:**

- **Bootstrap** (`_bootstrap_builtin_servers`): skips DB record creation. Single INFO log: `Builtin '<name>' skipped — package '<pkg>' not installed (pip install <pkg>)`.
- **Warmup pool** (`_init_warmup_pool`): skips pool registration. DEBUG log to avoid duplicating the bootstrap INFO.

The check runs **after** the `is_builtin_disabled()` env-var check so user intent (`MCP_DISABLE_BUILT_IN_*=true`) wins over package availability.

**No retries, no errors, no stacktraces** — the daemon starts cleanly without the missing built-in. Other built-ins (`webfetch`, `context7`, custom servers) and the rest of the system continue to work normally.

This is a **reusable pattern** for any built-in that depends on an optional Python package. To make a new built-in gracefully degrade when its package is missing, override `required_package` on the subclass — the base class handles the rest.

### Adding Custom MCP Servers

#### Via API: POST /api/mcp-servers

Create a new MCP server configuration with required fields:

**STDIO Transport Example:**

```json
{
  "name": "my-custom-server",
  "description": "My custom MCP server",
  "config": {
    "transport": "stdio",
    "command": "uvx",
    "args": ["my-mcp-server"],
    "env": {
      "API_KEY": "secret-key"
    },
    "timeout": 30.0
  },
  "is_active": true
}
```

**SSE Transport Example:**

```json
{
  "name": "my-sse-server",
  "description": "SSE-based MCP server",
  "config": {
    "transport": "sse",
    "url": "http://localhost:8080/mcp",
    "headers": {
      "Authorization": "Bearer token123"
    }
  },
  "is_active": true
}
```

**StreamableHTTP Transport Example:**

```json
{
  "name": "my-http-server",
  "description": "StreamableHTTP MCP server",
  "config": {
    "transport": "streamable-http",
    "url": "http://localhost:8080/mcp",
    "headers": {
      "X-API-Key": "my-api-key"
    }
  },
  "is_active": true
}
```

### How Agents Discover MCP Tools

Tools from MCP servers are automatically discovered and made available to agents:

1. **Naming Convention**: Tools are prefixed with `mcp_{server_name}_`
   - Example: A tool `browse` from server `webfetch` becomes `mcp_webfetch_browse`
   - Special characters are slugified (lowercase, hyphens→underscores)

2. **Discovery Timing**: Tool discovery happens at instance spawn time
   - The MCP service preloads tools before the agent graph executes
   - Tools are cached per-instance for fast retrieval

3. **Tool Injection**: Discovered tools are injected into the agent's tool list alongside built-in tools

4. **Description Enhancement**: MCP tool descriptions include `[MCP:server_name]` suffix for identification

### MCP Warmup Pool

The warmup pool eliminates the 5-15 second cold-start latency for STDIO-based MCP servers by maintaining pre-spawned, ready-to-use connections.

#### How the Pool Works

```
┌─────────────────────────────────────────────────────────────┐
│                     Warmup Pool                             │
│  ┌─────────────────┐  ┌─────────────────┐│
│  │ webfetch conn #1 │  │ context7 conn #1││
│  │ (ready)         │  │ (ready)         ││
│  └─────────────────┘  └─────────────────┘│
└─────────────────────────────────────────────────────────────┘
            │                    │
            ▼                    ▼
    ┌───────────────┐      ┌───────────────┐
    │ Instance #1   │      │ Instance #2   │
    │ (instant)     │      │ (instant)     │
    └───────────────┘      └───────────────┘
```

**Workflow:**

1. **Registration**: Built-in STDIO servers register with the pool at startup
2. **Warmup**: Pool spawns subprocess connections and completes MCP handshakes
3. **Tool Discovery**: Tools are discovered once and cached
4. **Acquire**: When an instance spawns, it acquires a pooled connection instantly
5. **Transfer**: Connection ownership transfers to the instance
6. **Replenish**: Background task replaces the acquired connection
7. **Health Checks**: Periodic pings ensure connections stay alive

#### Configuration Options

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_POOL_ENABLED` | `true` | Enable/disable warmup pool |
| `MCP_POOL_DEFAULT_POOL_SIZE` | `1` | Connections per server |
| `MCP_POOL_HEALTH_CHECK_INTERVAL` | `60` | Seconds between health checks |

**Per-Server Overrides:**

Configure server-specific pool sizes via config.yaml:

```yaml
mcp_pool:
  enabled: true
  default_pool_size: 1
  servers:
    webfetch: 2      # Override for webfetch
    context7: 1      # Override for context7
  health_check_interval: 60
  health_check_timeout: 5
```

**Disable Built-in Servers:**

Prevent specific built-in servers from being created:

```bash
MCP_DISABLE_BUILT_IN_WEBFETCH=true
MCP_DISABLE_BUILT_IN_CONTEXT7=true
```

### Transport Types

#### STDIO

Launches a subprocess and communicates via stdin/stdout. Best for local command-line tools.

**Configuration Fields:**

| Field | Type | Required | Description |
|-------|------|---------|-------------|
| `transport` | string | Yes | Must be `"stdio"` |
| `command` | string | Yes | Executable command (e.g., `uvx`, `npx`) |
| `args` | array[string] | No | Command-line arguments |
| `env` | object | No | Environment variables |
| `timeout` | number | No | Connection timeout in seconds (default: 30 seconds — null uses the 30s client default) |

**SSRF Protection:**

STDIO does not connect to URLs, but command validation is recommended. For HTTP-based servers, SSRF protection is enforced.

#### SSE (Server-Sent Events)

Connects to an HTTP server that streams events via SSE.

**Configuration Fields:**

| Field | Type | Required | Description |
|-------|------|---------|-------------|
| `transport` | string | Yes | Must be `"sse"` |
| `url` | string | Yes | HTTP endpoint URL |
| `headers` | object | No | HTTP headers |

**SSRF Protection:**

- Loopback addresses (127.x.x.x, ::1) allowed by default
- Private networks (10.x.x.x, 172.16-31.x.x, 192.168.x.x) allowed by default
- Link-local addresses (169.254.x.x) always blocked
- Set `MCP_ALLOW_LOCAL=false` to block local addresses for strict SSRF protection

#### StreamableHTTP

Modern HTTP-based transport using chunked transfer encoding.

**Configuration Fields:**

| Field | Type | Required | Description |
|-------|------|---------|-------------|
| `transport` | string | Yes | Must be `"streamable-http"` |
| `url` | string | Yes | HTTP endpoint URL |
| `headers` | object | No | HTTP headers |

**SSRF Protection:**

Same protection as SSE transport.

---

## MCP Server Mode (Exposing KB Tools Externally)

### Overview

agents-ensemble embeds a FastMCP server called `ensemble-kb` that exposes knowledge base tools to external AI agents and applications. This allows other AI systems to query and update the agents-ensemble knowledge base.

### Tools Exposed

| Tool Name | Description |
|-----------|-------------|
| `ensemble_kb_explore` | Search the knowledge base with various modes |
| `ensemble_kb_experience` | Record new knowledge into the knowledge base |
| `ensemble_kb_list_projects` | List all projects in the system |
| `ensemble_kb_search_projects` | Search projects by name or shortnames |

#### ensemble_kb_explore

Search the knowledge base for relevant information.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|---------|-------------|
| `query` | string | Yes | The question or topic to search for |
| `project_id` | string | No | Filter by project ID |
| `project_name` | string | No | Filter by project name |
| `project_path` | string | No | Filter by project path |
| `mode` | string | No | Search mode: `local`, `global`, `hybrid`, `naive` (default: `hybrid`) |

**Returns:** Knowledge base search results

#### ensemble_kb_experience

Record new knowledge into the knowledge base.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|---------|-------------|
| `text` | string | Yes | Knowledge text to record |
| `project_id` | string | No | Associate with project ID |
| `project_name` | string | No | Associate with project name |
| `project_path` | string | No | Associate with project path |

**Returns:** Confirmation message

#### ensemble_kb_list_projects

List all projects with optional filtering.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|---------|-------------|
| `limit` | number | No | Max results (default: 50) |
| `offset` | number | No | Skip N results (default: 0) |
| `status` | string | No | Filter by status (e.g., `active`) |

**Returns:** JSON array of project objects

#### ensemble_kb_search_projects

Search projects by name, description, or shortnames.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|---------|-------------|
| `query` | string | Yes | Search query string |
| `limit` | number | No | Max results (default: 20) |

**Returns:** JSON array of matching projects

### Transport Endpoints

The ensemble-kb server is mounted at two paths:

| Transport | Endpoint | Path |
|-----------|----------|------|
| StreamableHTTP | `/api/mcp/kb` | POST |
| SSE | `/api/mcp/kb/sse` | GET |

### Connecting External Agents

#### Example: OpenCode Configuration

Add to your OpenCode configuration file:

```json
{
  "mcpServers": {
    "ensemble-kb": {
      "url": "http://localhost:8079/api/mcp/kb"
    }
  }
}
```

For SSE transport:

```json
{
  "mcpServers": {
    "ensemble-kb": {
      "url": "http://localhost:8079/api/mcp/kb/sse"
    }
  }
}
```

#### Example: Claude Desktop Configuration

Add to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "ensemble-kb": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/client", "streamable-http", "http://localhost:8079/api/mcp/kb"]
    }
  }
}
```

Or using the SSE endpoint:

```json
{
  "mcpServers": {
    "ensemble-kb": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/client", "sse", "http://localhost:8079/api/mcp/kb/sse"]
    }
  }
}
```

#### Example: MCP Inspector

Test the server with MCP Inspector:

```bash
npx -y @modelcontextprotocol/inspector streamable-http http://localhost:8079/api/mcp/kb
```

---

## Managing MCP Servers

### API Endpoints Reference

All MCP server management endpoints are under `/api/mcp-servers`:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/mcp-servers` | List all MCP servers |
| `POST` | `/api/mcp-servers` | Create a new MCP server |
| `GET` | `/api/mcp-servers/{server_id}` | Get a specific server |
| `PUT` | `/api/mcp-servers/{server_id}` | Update a server |
| `DELETE` | `/api/mcp-servers/{server_id}` | Delete a server |
| `POST` | `/api/mcp-servers/test-connection` | Test server connectivity |
| `GET` | `/api/mcp-servers/builtin-templates` | List built-in templates |
| `POST` | `/api/mcp-servers/configure-builtin` | Configure a built-in server |
| `POST` | `/api/mcp-servers/{server_id}/reset-builtin` | Reset built-in to defaults |

### Built-in Templates

#### List Templates

```bash
GET /api/mcp-servers/builtin-templates
```

Response:

```json
{
  "templates": [
    {
      "name": "webfetch",
      "display_name": "WebFetch",
      "description": "Fetch and read web page content...",
      "config_schema": [
        {
          "key": "user_agent",
          "label": "User Agent",
          "type": "text",
          "description": "Custom User-Agent string...",
          "default": "Mozilla/5.0...",
          "required": false,
          "section": "args",
          "arg_format": "key_value"
        },
        {
          "key": "ignore_robots_txt",
          "label": "Ignore robots.txt",
          "type": "boolean",
          "description": "Bypass robots.txt restrictions...",
          "default": false,
          "required": false,
          "section": "args",
          "arg_format": "flag"
        }
      ]
    },
    {
      "name": "context7",
      "display_name": "Context7",
      "description": "Provides up-to-date library documentation...",
      "config_schema": []
    }
  ]
}
```

#### Configure a Built-in Server

```bash
POST /api/mcp-servers/configure-builtin
```

Request:

```json
{
  "template_name": "webfetch",
  "values": {
    "user_agent": "MyCustomAgent/1.0",
    "ignore_robots_txt": true
  }
}
```

### Testing Connections

Test if an MCP server is reachable before saving:

```bash
POST /api/mcp-servers/test-connection
```

Request:

```json
{
  "config": {
    "transport": "stdio",
    "command": "uvx",
    "args": ["mcp-server-fetch"]
  }
}
```

Successful response:

```json
{
  "success": true,
  "message": "Connection successful — server responded with 5 tools",
  "tools_count": 5
}
```

Error response:

```json
{
  "success": false,
  "message": "Connection failed: command not found"
}
```

---

## Configuration Reference

### MCP Server Schema

When creating or updating an MCP server, the following fields are used:

#### McpServerCreate

| Field | Type | Required | Description |
|-------|------|---------|-------------|
| `name` | string | Yes | Unique server name (1-128 chars) |
| `description` | string | No | Human-readable description |
| `config` | object | No | Transport configuration (see below) |
| `is_active` | boolean | No | Enable/disable server (default: true) |

#### McpServerInfo (Response)

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier |
| `name` | string | Server name |
| `description` | string | Server description |
| `config` | object | Transport configuration |
| `is_active` | boolean | Whether server is active |
| `is_builtin` | boolean | Whether this is a built-in server |
| `config_schema` | array | Configuration schema (for built-in) |
| `config_schema_version` | string | Schema version |
| `initial_values` | object | Values for form pre-fill |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

### Transport Configurations

#### STDIO Config

```json
{
  "transport": "stdio",
  "command": "string",
  "args": ["string"],
  "env": {"KEY": "value"},
  "timeout": 30.0
}
```

#### SSE Config

```json
{
  "transport": "sse",
  "url": "http://example.com/mcp",
  "headers": {"Authorization": "Bearer token"}
}
```

#### StreamableHTTP Config

```json
{
  "transport": "streamable-http",
  "url": "http://example.com/mcp",
  "headers": {"X-API-Key": "key"}
}
```

### ConfigSchemaField

Configuration field definition for built-in servers:

| Field | Type | Description |
|-------|------|-------------|
| `key` | string | Configuration key |
| `label` | string | Human-readable label |
| `type` | string | Type: `text`, `number`, `boolean`, `select` |
| `description` | string | Field description |
| `default` | any | Default value |
| `required` | boolean | Whether required |
| `options` | array | Options for `select` type |
| `min` | number | Minimum for `number` type |
| `max` | number | Maximum for `number` type |
| `section` | string | `args` or `env` |
| `arg_format` | string | `key_value` or `flag` |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_ALLOW_LOCAL` | `true` | Allow local addresses (127.x.x.x, 10.x.x.x, etc.) for MCP servers (default: allow, set `false` for strict SSRF blocking) |
| `MCP_ALLOW_LOOPBACK` | `true` | Backwards-compatible alias for `MCP_ALLOW_LOCAL` |
| `MCP_DISABLE_BUILT_IN_WEBFETCH` | `false` | Disable WebFetch built-in server |
| `MCP_DISABLE_BUILT_IN_CONTEXT7` | `false` | Disable Context7 built-in server |
| `MCP_POOL_ENABLED` | `true` | Enable warmup pool |
| `MCP_POOL_DEFAULT_POOL_SIZE` | `1` | Default pool size per server |
| `MCP_POOL_HEALTH_CHECK_INTERVAL` | `60` | Health check interval (seconds) |
| `MCP_POOL_HEALTH_CHECK_TIMEOUT` | `5` | Health check timeout (seconds) |

### SSRF Protection

For HTTP-based transports (SSE, StreamableHTTP), agents-ensemble validates URLs to prevent Server-Side Request Forgery attacks:

| Address Type | `MCP_ALLOW_LOCAL=true` (default) | `MCP_ALLOW_LOCAL=false` |
|--------------|----------------------------------|--------------------------|
| Loopback (127.x.x.x, ::1) | Allowed | Blocked |
| Private (10.x.x.x, 172.16-31.x.x, 192.168.x.x) | Allowed | Blocked |
| Link-local (169.254.x.x) | Blocked | Blocked |
| Reserved IPs | Blocked | Blocked |

DNS hostnames are resolved and checked against these restrictions.
