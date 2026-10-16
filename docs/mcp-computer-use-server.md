# Computer Use MCP Server (cu-mcp)

> **STATUS — INITIAL, 2026-10-16.** The macOS section is grounded in
> the live Phase-A probe on darwin-arm64 (host: 14.4.1; Node:
> v22.21.1) and the package's documented behavior. The **Windows**
> and **Ubuntu** sections are filled from the leader's wanderer
> round-2 cross-platform verification (`2026-10-16T08:13Z`,
> source: live npm tarball + repo verification). Today's
> integration is a local POC of the working MCP module inside
> ensemble; see the "Future work / scope" note at the bottom.

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
re-check the upstream `NATIVE-PACKAGES.md` semantics on any
**major** bump; **patch** bumps within a major (e.g. 7.6.x → 7.6.y)
are usually wire-compatible but still warrant a smoke pass on
the host. We are pinned to 7.6.1 today.

The `/mcp-servers/test-connection` UI probe (D7 guard at
`daemon/routers/mcp_servers.py`) detects the cu-mcp config by
matching the EXACT pinned package arg (e.g. `@zavora-ai/computer-use-mcp@7.6.1`)
in `args`, not by a prefix substring or by another structural
marker. The guard re-derives its expected pin from the SAME
`CU_NPX_ARGS` constant in this module's `__init__` (imported by
the router at line 137), so a pin bump is a single-file edit
to this module — the guard silently tracks it. The reason for
the exact-match (rather than startswith / regex) is the failure
mode: a future version with a NEWER pin (e.g. `@7.7.0`) MUST
NOT match the current guard, otherwise a future pin would
silently bypass the kill-switch without the guard being
updated. With the shared constant, that class of drift is
structurally impossible — a bump updates both seams atomically.

## Universal packaging (all OSes)

The 7.6.1 universal npm tarball bundles **all six platform
binaries** (`computer-use-napi.{darwin,linux,win32}-{arm64,x64}.node`,
~10.78 MB / 182 files). The `@zavora-ai/computer-use-mcp-<os>-<arch>`
subpackages that some package managers / tools may surface are
**manifests-only and intentionally unpublished** —
maintainer-confirmed upstream issue #36. They exist so a future
per-platform split has a stable handle, not because they install
real code today. Do not chase them. The universal tarball is the
source of truth and `npx -y @zavora-ai/computer-use-mcp@7.6.1`
picks the right binary for the host automatically.

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

### macOS (14+, this host 14.4.1)

**Native binaries bundled:** darwin-arm64 and darwin-x64 ship in
the 7.6.1 universal tarball (see "Universal packaging" above).
Phase-A probe verified the install-check and server boot on
darwin-arm64 / macOS 14.4.1 with Node v22.21.1; the server boots
cleanly and `tools/list` at `core` returns 38 tools. **Capture
backend: ScreenCaptureKit.**

**Mandatory TCC grants (System Settings → Privacy & Security):**
TCC attaches to the **host process** that launched the daemon
(the terminal app running `./dev.sh` in dev; the launchd job PID
in prod). The child `npx` subprocess inherits the grant from
the host, but TCC explicitly enumerates by process — so the
right app must be granted.

* **Accessibility** — required. Drives the input primitives
  (`click`, `type`, `key`, `mouse_move`, `scroll`, …) and the
  AX-based window/app helpers (`open_application`, `activate_app`,
  `list_windows`, `get_frontmost_app`, etc.). Without it the
  mutating tools will return errors at call time but the server
  still boots and `doctor` will report the gap.
* **Screen Recording** — required. Powers `screenshot`, `zoom`,
  and the OCR-based `read_window_text` / `click_text` tools via
  ScreenCaptureKit. Without it the capture tools return
  permission errors; the server still boots.

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
  4 seconds of input activity. **WITHOUT this grant the guard
  allows every call** — which is fine for unattended /
  automated flows but not for shared-host setups where a person
  is actively typing on the same Mac. The `core` profile uses
  the guard as designed; grant Input Monitoring for the daemon
  HOST terminal (Terminal.app / iTerm / Cursor / VS Code / etc.)
  when the daemon is shared. State this plainly: an unmonitored
  shared host is the highest-risk configuration.

**Platform bonus — OCR:** `read_window_text` (Apple Vision,
on-device, no network) and `click_text` are macOS-only.
On a Mac these give the agent cheap text + click-by-text
without screenshot round-trips, so macOS is the most ergonomic
target for text-heavy automation. The cross-platform build
provides OCR-via-vision backends elsewhere; macOS gets the
fastest path.

**Verification with `doctor`:** the package's built-in
`doctor` tool reports which TCC grants are in place and which
native binary compatibility checks pass. Run it once after the
first install; if it reports any required check as `MISSING`,
the affected tools won't work until the grant is fixed. The
daemon's first-call timeout for `doctor` is 120 s (matches
the rest of the cu-mcp tool surface — see
`CU_TOOL_CALL_TIMEOUT_SECONDS`).

### Windows

**Native binaries bundled:** win32-x64 and win32-arm64 ship
in the 7.6.1 universal tarball. The Windows backend has been
**native since v6.0** — first-class, not an afterthought.

**Display server:** Windows desktop (the Win32 window manager +
GDI/DirectComposition for rendering). AX layer is **UI
Automation** (not AT-SPI). Scripting layer (in higher profiles
— not in `core`) is **PowerShell**, not AppleScript / bash.

**Mandatory runtime prerequisites:**

* **Interactive signed-in desktop session** — the package
  attaches to the user's session; a session-0 Windows service
  has no input sink and **will not work**. The daemon MUST run
  in a normal interactive session (LocalSystem-as-interactive
  via `psexec -i`, the user's RDP session, a local logon, …).
  A launchd-style headless service is a no-go.
* **Protected / elevated windows** — to drive an elevated
  (UAC-elevated) target window, the daemon host process must
  run with matching privileges. The cleanest pattern is
  matching integrity: if the target is elevated, the daemon
  host must be too (run-as-admin); otherwise the input tools
  return `access_denied`. There is no TCC layer; Windows uses
  UAC + integrity levels + UIPI (User Interface Privilege
  Isolation) instead.
* **No TCC** — Windows does not have a Transparency, Consent,
  and Control layer. The relevant gates are UAC, UIPI, and the
  integrity-level matching above.

**Conditional / noted:**

* **`mouse_drag` and `multi_*` tools** (if/when a profile
  exposes them) require full Windows desktop session + matching
  integrity; not exposed in `core` so the POC is unaffected.
* **Known issue #32** — `agent_pointer` native-overlay deadlock
  on Win11 — was **fixed in v7.4.0**, which is well within our
  7.6.1 pin, AND `agent_pointer` is not in the `core` profile.
  The fix lands in a tool the user can never reach with our
  default profile: double-insulated. Real-Windows-desktop
  confirmation of the fix on a live Win11 host is still
  outstanding upstream — file an issue if you hit the regression
  path.

**Verification with `doctor`:** the package's `doctor` tool
runs UI Automation + graphics-stack checks; the daemon host's
first-call timeout is 120 s.

### Ubuntu (Linux)

**Native binaries bundled:** linux-x64 and linux-arm64 ship
in the 7.6.1 universal tarball. **glibc only** — Alpine,
musl-based distros, and other non-glibc libc implementations
are NOT supported. The `@zavora-ai/computer-use-mcp-<os>-<arch>`
subpackages that some tooling surfaces are **manifests-only
and intentionally unpublished** (maintainer-confirmed upstream
issue #36) — the universal tarball is the source of truth. Do
not chase the per-arch subpackages.

**Display server — X11 vs Wayland, the two paths take
DIFFERENT code:**

* **X11 session (RECOMMENDED, fully supported for `core`).**
  Mouse + key synthesis rides the **XTest extension** (and
  XInput where applicable). The recommended supported config
  is an X11 login session. Tools that depend on synthetic input
  (`click`, `type`, `key`, `mouse_move`, `scroll`, `screenshot`)
  work out of the box. To run a daemon under X11 you need a
  real `XAUTHORITY` + `DISPLAY` (e.g. start a headless Xvfb
  or attach to the user's logged-in X11 session via
  `xhost +SI:localuser:<daemon-user>`).
* **Wayland (Ubuntu 24.04 default).** Synthetic input is NOT
  available through the standard Wayland protocol — the
  compositor forbids it. The package delegates to **`ydotool`**
  for `click` / `type` / `key` / `scroll` / `mouse_move`,
  which writes events through `/dev/uinput` via the
  `ydotoold` daemon. The `core` primitives work in Wayland
  sessions when the daemon's host user is a member of the
  `uinput` group AND `ydotoold` is running. **Caveat:**
  `mouse_drag` and `multi_select` / `multi_edit` (exposed by
  higher profiles, not in `core`) are **X11-only regardless
  of session** — they don't have a Wayland back-end.

**AX tier (GTK / Qt element-based automation) — Linux only,
AT-SPI bridge since v7.2** (`libexec/linux-atspi.py`). To use
the AX element tools (`get_ui_tree`, `find_element`,
`click_element`, `set_value`, `press_button`, `fill_form`,
`list_menu_bar`, etc.) on a GTK app you need the GTK AT-SPI
bridge enabled in the user's session:

```sh
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

Qt apps need their own bridge (typically `qt-at-spi`); the
package doesn't ship a Qt-specific bridge. **One open,
unconfirmed upstream issue #21** affects the AT-SPI bridge's
reliability on some element paths; **do NOT promise
element-based automation on Linux** until that issue is
confirmed resolved. The `core` profile does not require
element-based automation, so the POC is unaffected; bumping
to a profile that does (e.g. `ax` or `full`) on Linux
without confirming issue #21 is at your own risk.

**Other prerequisites:**

* **systemd-logind / seat session** — the daemon's process
  must be in the same seat as the user's active session for
  the X11 / Wayland access to work. A systemd unit with
  `StandardEnvironment=DISPLAY=:0` (X11) or a corresponding
  user-session env (Wayland) is the standard pattern.
* **libdbus / libatk-bridge2.0** — runtime deps the
  `libexec/linux-atspi.py` bridge pulls in transitively; if
  `doctor` reports AT-SPI check failure on a host that should
  support it, check `dpkg -l | grep at-spi2-core`.

**Verification with `doctor`:** the package's `doctor` tool
runs the X11 / AT-SPI / `ydotool` / uinput presence checks.
Run once after install on a fresh host. The daemon host's
first-call timeout for `doctor` is 120 s.

**Proven install path (today):** X11 login session on
Ubuntu 22.04+ x86_64, with a standard `apt`-installed
at-spi2-core + a logged-in user whose session the daemon
can attach to. Wayland on Ubuntu 24.04 with `ydotool` +
`ydotoold` running is supported in the `core` primitives;
AX tier and `mouse_drag` are not.

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

## Future work / scope

Today's integration is a **local POC of the working MCP module
inside ensemble**: ari / leader drive a single daemon host's
desktop via stdio transport. Remote / HTTP transport plus
fleet-gateway routing — remote-controlling many registered
Ubuntu / Windows machines from a single ensemble instance — is
**explicitly future work in a separate fleet project** and is
out of scope here. This doc covers the local-host seam only;
the fleet project will own the remote transport, the registry
of remote machines, and the per-target routing layer.

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
