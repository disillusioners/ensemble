# Lesson: load_skill pre-flight vs empty tools.allow + MCP tools documented-but-unbound (2026-10-02)

Context: OpenDesign install independent-verification commission (see RESULTS/2026-10-02-opendesign-install-independent-verification.md).

## Finding F1 — load_skill pre-flight defect

- Symptom: `send_message(..., load_skill="opendesign-verify")` to a WORKER instance terminated the child immediately with:
  `{"kind":"capability_missing","capability":"bash","detection_evidence":"pre_flight: tools.allow.contains('bash') -> not in []"}`
  — zero tool calls executed ([REPORT SANITY] marker confirmed).
- The worker flavor DOES have working bash (sibling worker ran a full bash session in the same wave). So the pre-flight misreads an EMPTY declarative `tools.allow` as "bash not allowed".
- Workaround that works TODAY: dispatch WITHOUT load_skill; instruct the worker to `skill_search(query=...)` → `skill_view(skill_id)` and execute the skill body manually, then `skill_feedback(...)` for attribution. Verified end-to-end on 2026-10-02 (AC3 PASS).
- Fix owner: ensemble skill-runner/pre-flight (check empty-allow semantics = universe, per tool-filter conventions).

## Finding F2 — MCP tools documented in prompt inventory but not bound at runtime

- Symptom: `mcp_opendesign_*` tools appear in the system-prompt tool table, but calling them returns "not a valid tool" and the runtime valid-tool list contains NO mcp_* entries.
- Reproduced 3× on fresh non-leader instances (tester + 2 workers). Leader toolset reportedly works.
- Consequence: fresh instances must fall back to MCP stdio JSON-RPC against the registered proxy (works fine: initialize → tools/list → tools/call, steady-state 3–83 ms) or the daemon REST routes (GET /api/projects, /api/health, /api/version).
- Pattern to remember: PROMPT TOOL TABLE ≠ RUNTIME TOOL BINDINGS. When a dispatch needs an MCP tool, assume unbound on children; spec the JSON-RPC fallback in the task message up front.

## Minor notes

- systemd --user commands in detached worker shells need `export XDG_RUNTIME_DIR=/run/user/1000` + `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`, else "Failed to connect to bus: No medium found".
- OpenDesign seam arg names are camelCase (`projectId`, `prompt`), not snake_case — read tools/list inputSchemas before composing tools/call.
- "Proxy binary" claims: npm .bin entries are bin-shim SYMLINKS — verify with ls -l before calling something a binary.
