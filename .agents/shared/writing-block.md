# Hybrid Tool Architecture — Stable Tool Registry

## Objective

Optimize prompt-cache hit rates in long-running agent conversations by keeping model-facing tool definitions stable while allowing agents to access dynamically registered tools.

## Architecture

Adopt a three-tier tool architecture:

### 1. Core Tools (Direct Exposure)
Frequently used tools are exposed directly to the LLM with complete JSON schemas.

Examples:
- `read_file`
- `write_file`
- `execute_command`
- `search_code`
- `delegate_task`

These definitions should remain stable across requests.

### 2. Extended Tools (Generic Dispatcher)
Rarely used tools are accessed through a small set of generic tools:

- `call_tool(name, arguments)` — Execute a registered tool.
- `list_tools(category)` — Discover available tools and their descriptions.

The LLM can discover and invoke extended capabilities without modifying the main tool definitions.

### 3. Dynamic Tool Registry
The backend maintains the actual tool registry, including:
- MCP tools
- Kubernetes operations
- Database administration
- Cloud infrastructure
- External integrations

Tools can be registered, updated, or removed dynamically without changing the model-facing tool schemas.

## Execution Flow

1. Agent requests available tools using `list_tools(category)`.
2. Backend returns matching tool names and concise descriptions.
3. Agent selects a tool and calls `call_tool(name, arguments)`.
4. Backend resolves the registered tool, validates arguments against its actual schema, checks permissions, and executes it.
5. Execution results are returned to the agent.

## Design Principles

- **Stable interfaces:** Keep model-facing tool definitions and ordering unchanged.
- **Dynamic capabilities:** Allow backend tool registration and updates without modifying the LLM request's tool definitions.
- **Progressive discovery:** Retrieve detailed tool schemas only when necessary.
- **Security:** Enforce authorization and argument validation server-side. Never rely on LLM tool selection as an authorization mechanism.
- **Efficiency:** Keep frequently used tools directly exposed to avoid unnecessary dispatcher calls.

## Expected Benefits

- Better prompt-cache reuse across conversation turns.
- Reduced repeated prefill costs for long-running agents.
- Dynamic MCP and external tool integration.
- Smaller model-facing tool definitions.
- Clear separation between agent capabilities and tool execution infrastructure.

## Guiding Principle

**Dynamic capabilities, static interfaces.**

Expose frequently used tools directly; route rarely used and dynamically changing capabilities through a stable generic dispatcher.