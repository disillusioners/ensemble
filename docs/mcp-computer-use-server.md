# Computer Use MCP Server (cu-mcp)

> **STATUS — DRAFT, 2026-10-16.** This document is in DRAFT STATE
> awaiting the cross-platform verification round from the leader.
> The macOS section is grounded in the live Phase-A probe on
> darwin-arm64 (host: 14.4.1; Node: v22.21.1) and the package's
> documented behavior. The **Windows** and **Ubuntu** sections are
> PLACEHOLDERS — they will be filled once the leader's verified
> per-OS facts arrive. Do NOT consolidate the placeholders into
> canonical copy before that round completes.

The Computer Use MCP built-in gives the `ari` and `leader` agents
the ability to drive a desktop GUI on the daemon host — mouse,
keyboard, screenshots, OCR, accessibility tree, window/app
management — through the cross-platform
[`@zavora-ai/computer-use-mcp`](https://github.com/zavora-ai/computer-use-mcp)
package. See [`mcp-integration.md`](./mcp-integration.md) for the
generic MCP wiring story; this document is the per-server, per-OS
operations guide.

## How it's wired

Built-in MCP server (`daemon/mcp/builtin_servers/computer_use.py`)
auto-registers on import alongside context7, plane, and webfetch.
The bootstrap layer (`InstanceManager._bootstrap_builtin_servers`)
provisions a DB row with the schema-version-pinned config; the
drift-refresh logic heals rows that lag behind the canonical
schema. `schema_version` is bumped (e.g. `1 → 2`) ONLY when the
config schema changes — a pin bump in `@<version>` alone is NOT
schema drift and should NOT bump the version.

When registered, tools surface to ari/leader as `cu_click`,
`cu_screenshot`, `cu_doctor`, etc. (prefix-overridden — see
`DYNAMIC_TOOL_PREFIXES` in `daemon/tools/_tool_registry.py`).
Stamping `_tool_category="cu"` on every prefix-overridden tool
makes the privilege strip
(`_strip_privileged_category_tools` in
`daemon/tools/instance.py`) catch them in empty-allow universes
— the same protection first-party privileged tools get.

## Which agents and how to extend

**Default access list:** `ari` and `leader` ONLY. The
`agents/ari/meta.json` and `agents/leader/meta.json` allow arrays
each carry a `"cu"` entry. No other agent (watcher, planner,
developer, designer, …) gets cu tools — adding a new agent is
a meta.json edit. The empty-allow universe (e.g. `watcher`) MUST
NOT auto-inherit.

**To grant additional agents:**
1. Append `"cu"` to `agents/<agent_id>/meta.json`'s
   `tools.allow` array.
2. Restart the daemon (or rely on meta-cache invalidation).
3. The privilege category `cu` (in
   `PRIVILEGED_TOOL_CATEGORIES`) gates the privilege strip
   — explicit `"cu"` entry is the ONLY access path.

**Schema version drift policy:** only bump `schema_version` when
the config shape (env keys, args, transport) changes — pin
upgrades are config-shape-preserving and stay at the same
version.

## Version-pin policy

The package pin (`@zavora-ai/computer-use-mcp@<x.y.z>`) lives in
**one place**: module-level constants in
`daemon/mcp/builtin_servers/computer_use.py` (`CU_NPM_PACKAGE`,
`CU_NPX_ARGS`). A package swap (e.g. zavora → domdomegg per the
leader's cross-platform round) is a SINGLE-FILE edit to that
module's pin constants — plus the corresponding test constant
(`_CU_MCP_TEST_NPX_ARGS` in `tests/unit/test_computer_use_mcp.py`).
No other file (router, tool registry, other agents' meta.json) is
allowed to hardcode the pin string or the profile value
(`CU_DEFAULT_PROFILE`).

A bump is a deliberate act because the v7.x line has documented
wire-format churn across patch versions — an unpinned
`npx -y @zavora-ai/computer-use-mcp` would auto-pull the latest
7.x at every fresh cache, silently shifting the tool surface,
schema, and per-platform support flags. Re-verify wire shapes and
re-run `tests/unit/test_computer_use_mcp.py` BEFORE cutting the
pin.

## Kill-switches

| Switch | Default | Effect |
|--------|---------|--------|
| `COMPUTER_USE_MCP_ENABLED` | ON (unset) | Primary kill-switch; falsy values `0`/`false`/`no`/`off` (case-insensitive, whitespace-stripped) disable |
| `MCP_DISABLE_BUILT_IN_COMPUTER_USE` | OFF | Framework-level generic disable (per `daemon.mcp.builtin_servers.is_builtin_disabled`) |
| `CU_NPX_PATH` | (PATH lookup) | Absolute path to the `npx` binary — for sandboxed launchd agents or PATH-stripped environments |

When the primary switch is OFF OR npx is unresolvable
(`_resolve_cu_npx()` returns None) OR the generic disable is set,
`ComputerUseServerDefinition.is_available()` returns `False` and
the bootstrap / warmup layers skip the server entirely (no DB
record, no connection attempt). The kill-switch path emits ONE
precise log line per process at the first availability check
(mirrors `daemon/mcp/builtin_servers/plane.py`); the npx-
unresolvable path emits a distinct remediation hint line.

The kill-switch is also enforced at the `/mcp-servers/test-
connection` UI probe (D7-shaped hole fix in
`daemon/routers/mcp_servers.py`) so an operator who turns the
switch off still can't probe the binary via the admin UI.

## Loopback console

The package exposes a separate console companion
(`computer-use-mcp-console` bin) that binds to
**`http://127.0.0.1:4517`** — loopback-only by design (the
underlying server refuses to bind any address but the loopback).
This is NOT started by the stdio MCP server launched in our
build-config; it ships as a separate binary for operators who
want a local control surface during interactive sessions.

The stdio MCP server also registers an MCP UI-extension
resource (`ui://computer-use/run-console/v1`, `profile=mcp-app`)
that some hosts render inline. The TCP listener on :4517 only
comes up if the console companion is launched as its own
process.

## Safety notes

* **Full local-control surface** — when the host OS grants are
  present, the `cu_*` mutating tools can drive every focused
  app on the host. Treat the grants like root-level access.
* **No API keys, no telemetry.** The package is fully on-host.
* **Kill-switch disables without rebuild** — set
  `COMPUTER_USE_MCP_ENABLED=false` (and restart, because the
  builtin row is created at bootstrap), or use `mcp-disable`
  on a running session through the admin UI.
* **Approval expectations for dangerous actions** — the
  `policy_status` tool reports the active policy; the
  user can pause/stop the agent from a desktop status card
  (see `control_status` tool). Mutating tools return
  `paused_by_user` or `stopped_by_user` while paused/stopped.
* **Sensitive apps gated** — password managers and similar
  tools are gated by the package's policy layer
  (consult `policy_status` for the active state).
* **Mandatory privacy/security grants** — see the per-OS
  sections below.

---

## Per-OS Notes

### macOS

The Phase-A probe ran on darwin-arm64 / macOS 14.4.1 with Node
v22.21.1; the server boots cleanly (install-check passes) and
returns 38 tools at the `core` profile (verified 2026-10-16).

**Mandatory TCC grants (System Settings → Privacy & Security):**

* **Accessibility** — required. Allows AX-based window/app
  discovery and click-element-by-role semantics. Without it,
  click/type/key tools will return errors at call time but the
  server still boots and `doctor` will report the gap.
* **Screen Recording** — required. Allows screenshot, zoom,
  and the OCR-based read_window_text / click_text tools. TCC
  attaches to the **host process** that launched the daemon
  (the terminal app running `./dev.sh` in dev; the launchd
  job PID in prod). The child `npx` subprocess inherits the
  grant from the host, but TCC explicitly enumerates by
  process — so the right app must be granted.

**Conditional grant:**

* **Automation** (System Settings → Privacy & Security →
  Automation) — only if your agent invokes scripting tools
  that drive other apps via AppleScript (`run_script`,
  scriptable apps). The `core` profile (the project default)
  does NOT expose `run_script`; `desktop` and `ax` profiles
  may. Verify with `doctor`'s policy section before assuming
  you need this grant.

**Optional but recommended:**

* **Input Monitoring** (System Settings → Privacy & Security →
  Input Monitoring) — enables the user-active guard that
  blocks mutating calls that would interrupt the user within
  4 seconds of input activity. WITHOUT this grant the
  user-active guard is open and any call may proceed — which
  is fine for unattended/automated flows but not for
  shared-host setups where a person is actively typing on the
  same Mac. The `core` profile uses the guard as designed;
  grant Input Monitoring for the daemon HOST terminal
  (Terminal.app / iTerm / Cursor / VS Code / etc.) when the
  daemon is shared.

**Verification with `doctor`:** the package's built-in
`doctor` tool reports which TCC grants are in place and which
native binary compatibility checks pass. Run it once after the
first install; if it reports any required check as `MISSING`,
the affected tools won't work until the grant is fixed. The
daemon's first-call timeout for `doctor` is 120 s (matches
the rest of the cu-mcp tool surface — see
`CU_TOOL_CALL_TIMEOUT_SECONDS`).

### Windows

<!-- PENDING: per-OS facts from leader. The leader is re-verifying the
zavora install + primitives story on Windows in parallel; the live
smoke is round-1 work in progress. Once the leader's verdict lands,
fill in this section with: mandatory privileges (UAC / Defensive
UIAC), per-profile native binary status, the loopback console path
on Windows (does :4517 bind the same?), the npm shim situation, and
any ImageHelpers / DirectComposition caveats the package hits. -->

### Ubuntu — incl. X11 vs Wayland note

<!-- PENDING: per-OS facts from leader. The leader is re-verifying the
zavora install + primitives story on Ubuntu in parallel. Once the
verdict lands, fill in this section with: how the package handles
each of the two display-server paths (X11 vs Wayland — at minimum
the document must note that they take DIFFERENT code paths in the
native addon: X11 uses XTest + XInput extension for mouse + key
synthesis; Wayland requires a privileged portal (xdg-desktop-portal
+ gnome-remote-desktop / krfb) and the package's Wayland support is
often experimental), the dependencies needed on each
(`libxcb-*` / `xdotool` for X11 vs portal setup for Wayland), the
Audio/A11y bus (AT-SPI) toggles, and the systemd-logind / seat
session prerequisites so the daemon's systemd unit sees the active
display. The current `core` profile passes the bundle-native
checks on Apple Silicon + macOS; cross-platform viability on
Ubuntu is the open question — do NOT claim "supported on Ubuntu"
before the leader's round lands. -->

---

## Vision + non-vision flows

This server is **non-vision-first by design choice**. ensemble's
vision capability is opt-in via the `OPENAI_MODEL_VISION` env
var (unset = no vision); the `image` tool category follows.
Computer-use ships its own OCR + AX-tree + image-with-path flow
because the human-driven interface often does NOT need vision
on the model side:

* **Vision-capable agents** (`OPENAI_MODEL_VISION` set): receive
  image blocks via `screenshot` / `zoom` directly. The LLM sees
  the screen.
* **Non-vision agents**: consume OCR text via `read_window_text`
  (Apple Vision on macOS — see caveats in the upstream docs), AX
  labels via `get_ui_tree`, and use `run_attachment` image-
  with-path tools when they need a stored screenshot inspected
  by a separate vision-capable call (or by the user). The image
  content lives on disk as a file path; the model never sees
  the pixel bitmap.

This split is deliberate: it keeps non-vision agents useful on
desktop control without forcing ensemble-wide vision to be ON
for everyone. Agents that need vision set the env var; agents
that don't, don't have to.

## Linked documents

* [`docs/mcp-integration.md`](./mcp-integration.md) — generic
  MCP wiring (built-in servers, transport, drift-refresh).
* `daemon/mcp/builtin_servers/computer_use.py` — the single
  source of truth for the package pin, profile, prefix, and
  kill-switch.
* `daemon/tools/_tool_registry.py` — `DYNAMIC_TOOL_PREFIXES`
  (adds `cu_`) and `PRIVILEGED_TOOL_CATEGORIES` (adds `cu`).
* `tests/unit/test_computer_use_mcp.py` — kill-switch,
  prefix-override, privilege-stamp, bootstrap-skip, drift-
  refresh, npx-unavailable, empty-allow-leak regression,
  D7-guard, registry sanity (45 tests).
