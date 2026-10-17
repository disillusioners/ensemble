# cu-mcp live smoke: macOS TCC wallpaper-only signature, JPEG payloads, doctor authority

Date: 2026-10-16
Context: task cu-mcp live benign smoke on macOS 14.4.1 (RESULTS/2026-10-16-cu-mcp-computer-use-verification.md)

## Findings

1. **TCC partial-denial: screenshot metrics LIE, doctor tells the truth.** Without Screen Recording granted to the harness host, the zavora server still returns a *non-black, numerically non-trivial* image (68 KB JPEG, 1024×640, mean 123.5, stddev 71.7, 21,567 unique colors) — because macOS composites the desktop wallpaper without app windows. The classic "all-black capture" heuristic does NOT fire on this stack. The authoritative grant signal is the `doctor` tool (`display_capture` / `accessibility` checks with exact remediation strings). Any future screen-capture verification MUST gate on doctor, not on image statistics.
2. **zavora screenshot payload is JPEG, not PNG.** Magic-byte sniff before decode; worktree venv has no PIL — `sips` converts JPEG→PNG for pure-python metric decoding.
3. **TCC grant target = the terminal hosting the ensemble daemon chain.** Process tree: Terminal.app → zsh → bash → ensemble daemon → driver → npx → node. Grant Terminal.app BOTH Screen (& System Audio) Recording and Accessibility, then RESTART the chain (TCC changes require process restart). No workarounds (tccutil/sudo/DB) per safety line.
4. **Structural allowlist > prompt discipline for safety-critical smokes.** The driver enforced `ALLOWED_TOOLS` in code (`call_tool_safe()` refuses everything else, logs every call pre-exec). 38-tool surface, 4 called, 34 refused — including ambiguous names (zoom) skipped by rule.
5. **read_window_text needs a target** (`target_app` bundle ID or `target_window_id`) — bare invocation is server-refused with `target_required`. OCR verification therefore needs window enumeration first (not allowlisted this round).
6. **Sequencing rule (new):** in TCC-gated smokes, run `doctor` BEFORE capture-dependent calls and branch on its verdict — metrics-based gating fired calls 3–4 after doctor had already reported denial (disclosed, harmless here, but the rule going forward is doctor-first).
7. **Kill-switch OFF-path grep gotcha:** substring greps for spawn-evidence (`npx`, package name) false-positive on worktree paths containing the feature name (data dirs under `wt-cu-mcp/...`) — path-strip filter + hard counters required.

## Reuse

Harness pattern (worktree boot-e2e script + env overrides + canary + SIGTERM-trap teardown + port-free assertion) is the canonical way to live-smoke MCP builtins without touching prod. Ports used this cycle: 15433/18099 (ON-path), 15434/18198 (OFF-path) — both freed.
