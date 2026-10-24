# Computer Use MCP Server (cu-mcp) — REMOVED

> **STATUS — REMOVED 2026-10-24** (branch `feature/remove-cu-builtin`).
> This page is kept as a **historical stub** so the link from
> [`agents/gaia/workflow.md`](../agents/gaia/workflow.md) keeps
> resolving. It is NOT operator documentation any more — the daemon no
> longer ships a computer-use MCP server.

## What was removed

The daemon-side **computer-use builtin MCP server** is gone. Concretely,
the following were deleted:

| Removed | What it was |
|---|---|
| `daemon/mcp/builtin_servers/computer_use.py` | The builtin definition — the `ComputerUseServerDefinition` class, the `@zavora-ai/computer-use-mcp@7.6.1` npx package pin (`CU_NPM_PACKAGE` / `CU_NPX_ARGS`), the `core` profile default (`CU_DEFAULT_PROFILE`), the `cu` tool-name prefix (`CU_TOOL_NAME_PREFIX`), the per-call timeout (`CU_TOOL_CALL_TIMEOUT_SECONDS`) and the schema version (`CU_SCHEMA_VERSION`). |
| `daemon/tools/cu_tools.py` | The docstring-only category anchor for the `cu_*` tool namespace. |
| The `cu` registry entry | `daemon/mcp/builtin_servers/__init__.py` no longer imports or registers `ComputerUseServerDefinition`. The three surviving builtins (`webfetch`, `context7`, `plane`) are untouched. |
| The `cu` privileged category | `daemon/tools/_tool_registry.py` — `PRIVILEGED_TOOL_CATEGORIES` goes **16 → 15** members (the daemon-internal authority trio `system_upgrade` / `system-log` / `ens-db` and every other member, including `mcp_rpc`, are unchanged). The `cu_` entry leaves `DYNAMIC_TOOL_PREFIXES` (now `{"plane_"}`) and the `cu` row leaves `CATEGORY_MODULES`. **No dynamic prefix stamps a category any more** — `plane` is deliberately not privileged. |
| The `cu` agent allow-entries | `agents/ari/meta.json` and `agents/leader/meta.json` no longer list `cu` in `tools.allow`. Both keep `mcp_rpc`. |
| The cu-specific D7 probe guard | `daemon/routers/mcp_servers.py` — the `_block_test_connection_when_builtin_unavailable` helper (which detected the cu npx pin specifically) and its single call site. The `/mcp-servers/test-connection` probe is otherwise unchanged. |

### Dead environment variables

These are **no longer read by the daemon** — leaving them set in an
`.env` is inert and harmless:

- `COMPUTER_USE_MCP_ENABLED`
- `MCP_DISABLE_BUILT_IN_COMPUTER_USE`
- `CU_NPX_PATH`
- `COMPUTER_USE_PROFILE`

### The `cu_*` tool namespace

The prefix-overridden `cu_*` tool names (`cu_click`, `cu_screenshot`,
`cu_doctor`, …) no longer exist. Remote-desktop control now flows through
the **`mcp_rpc`** tool category instead.

## What superseded it

The replacement path is the **remote-hands** stack, which is live and
pilot-proven end-to-end (real Go client × real Python gateway over TLS)
*before* this removal:

- a **`mcps_client`** wrapper — a signed macOS `.app` that runs the
  computer-use MCP server on the controlled machine and dials the
  gateway outbound;
- the **`McpGatewayService`** — the daemon-side center (roster + sessions
  + heartbeat monitor + liveness evidence + audit rows);
- the **`mcp_rpc`** tool category — the agent-facing surface
  (`mcp_rpc_list_machine` / `mcp_rpc_connect` / `mcp_rpc_command` /
  `mcp_rpc_disconnect`), privileged and opt-in-only (`ari` + `leader`).

The remote machine owns its own local-control surface and TCC grants; the
daemon no longer spawns or supervises a computer-use subprocess.

### Pointers

- [`docs/mcps-client-remote-hands.md`](./mcps-client-remote-hands.md) —
  the replacement architecture (design reference; still accurate).
- [`docs/mcps-gateway.md`](./mcps-gateway.md) — the gateway surface and
  the `mcp_rpc` authority model.
- [`agents/gaia/workflow.md`](../agents/gaia/workflow.md) — the wrapper
  lifecycle runbook (TCC, LaunchAgent, enrollment).
- The standalone **`mcps_client`** project (the Go wrapper + `.app`).
- The gateway endpoint at `public.ensem.dev`.

## Database note

The `mcp_servers` row named `computer-use` was **soft-deactivated**
(`is_active=false`) out of band — no schema change, no row deletion. The
boot orphan sweep (`_deactivate_orphaned_builtin_servers`) deactivates
any re-appearance of the row, so a stale copy can never re-activate
itself.

## The original document

The full 440-line operator guide (per-OS sections, the package pin
policy, TCC notes, the `core` profile rationale, the D7 kill-switch
matrix) lives in git history — read it at the commit that last touched
it:

```bash
git show 0774f3fe1:docs/mcp-computer-use-server.md
```

`0774f3fe1` ("docs: reviewer suggestions — comment/doc truth for S1 gate
semantics") was the last commit to touch this file before the removal.
The cu builtin's own source (`daemon/mcp/builtin_servers/computer_use.py`)
has its own history — its last touch before the removal was `a6c32de04`
("[cu-mcp] S2: docstring truth-fix — is_available() is a 3-gate AND, not
4-gate"):

```bash
git show a6c32de04:daemon/mcp/builtin_servers/computer_use.py
```
