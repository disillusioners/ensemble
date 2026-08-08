# Phase 4: Agent Permission Integration

## Objective

Add `"system-log"` to the `tools.allow` list in the `meta.json` of the four target agents AND update each agent's prompt documentation (`tools_note.md` creation/update + `soul.md` tool inventory) per `docs/agent-prompt-writing-guide.md` convention. The prompt file updates are BLOCKING deliverables — they ensure agents know the tools exist, understand their read-only nature, and follow the project's standard agent prompt conventions. Without prompt updates, the feature is incomplete per `docs/agent-prompt-writing-guide.md` (the cardinal/guideline split mandates tools are documented in `tools_note.md`, not buried elsewhere).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add `"system-log"` to `tools.allow` in `agents/leader/meta.json`, `agents/developer/meta.json`, `agents/worker/meta.json`, `agents/wanderer/meta.json` | Phase 3 complete | All four meta.json files have `"system-log"` in their `tools.allow` array |
| 2 | Create/update `tools_note.md` for leader, developer, worker, wanderer | Task 1 | Each agent has a `tools_note.md` with a `## System Log` section documenting `ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail` — read-only, redaction, security constraints |
| 3 | Update `soul.md` tool inventory for all 4 agents | Task 1 | Each agent's `soul.md` tool inventory table/lists include the 4 system-log tools with the "system-log" category |
| 4 | Update phase3-plan references from "four target agents" to reflect 4 tools | Tasks 1-3 | All cross-references consistent |

## Detailed File Changes

### Task 1: `meta.json` updates (one-line addition per file)

#### `agents/leader/meta.json`

**Current `tools.allow`:**
```json
["instance", "self", "project", "help", "image", "knowledge", "mcp", "critical_notes", "project_history", "shared_context", "question"]
```

**After:**
```json
["instance", "self", "project", "help", "image", "knowledge", "mcp", "critical_notes", "project_history", "shared_context", "question", "system-log"]
```

**Rationale:** Leader is the top-level orchestrator. It needs log access to diagnose failures in spawned instances and the daemon itself. Placed at the end of the list (alphabetical-ish, consistent with existing ordering style).

---

#### `agents/developer/meta.json`

**Current `tools.allow`:**
```json
["bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_context", "db", "blueprint"]
```

**After:**
```json
["bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_context", "db", "blueprint", "system-log"]
```

**Rationale:** Developer is the primary code-writing agent. Log access enables it to debug runtime issues, verify code changes didn't break the daemon, and trace errors in spawned instances.

---

#### `agents/worker/meta.json`

**Current `tools.allow`:**
```json
["bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_context", "dynamic-skill"]
```

**After:**
```json
["bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_context", "dynamic-skill", "system-log"]
```

**Rationale:** Worker is a focused executor. Log access enables self-healing — a worker that encounters an error can check the daemon logs to understand the root cause before reporting back.

---

#### `agents/wanderer/meta.json`

**Current `tools.allow`:**
```json
["bash", "proc", "filesystem", "time", "self", "help", "explore", "mcp", "context", "shared_context", "rag", "instance", "blueprint"]
```

**After:**
```json
["bash", "proc", "filesystem", "time", "self", "help", "explore", "mcp", "context", "shared_context", "rag", "instance", "blueprint", "system-log"]
```

**Rationale:** Wanderer is an investigation/exploration agent (recently migrated from coder to worker per the project history). Log access directly supports its investigation role — reading daemon logs is a natural diagnostic action.

---

**Note on other agents:** Tester, architect, planner, charter, explorer, governor, skill-keeper, blueprinter, doc-maintainer, and other specialized agents are intentionally excluded from this phase. The feature targets the four core agents (leader, developer, worker, wanderer) that are most likely to need self-healing log access. Other agents can be granted the category in follow-up PRs if needed.

### Task 2: `tools_note.md` content per agent (BLOCKING — not optional)

Per `docs/agent-prompt-writing-guide.md`, `tools_note.md` is the canonical home for tool documentation (the cardinal/guideline split: meta.json controls WHAT tools are loaded; tools_note.md documents HOW to use them). For each of the four target agents:

**All 4 agents (leader, developer, worker, wanderer):** Create or update `tools_note.md` with the following `## System Log` section:

```markdown
## System Log

Read-only access to the daemon's own log files under `data/logs/` for
self-healing — investigate runtime bugs by inspecting log output.

**Available tools (category: `system-log`):**
- `ens_system_log_list` — List available log files with sizes and last-modified timestamps
- `ens_system_log_read` — Paged read of log lines with line numbers (offset/limit)
- `ens_system_log_search` — Regex search with context lines and optional level filter
- `ens_system_log_tail` — Read last N lines (tail equivalent) with optional level filter

**Security:** All output is redacted — API keys, tokens, passwords, and
Bearer tokens are replaced with `[REDACTED]`. Path traversal is blocked.
Maximum 500 lines / 12KB per response.
```

**Per-agent additions (optional but recommended):**

- **leader:** Add note: "When diagnosing failures in spawned instances, use these tools to inspect the daemon's own logs first before asking the user. Spawned instances often leave error traces here."
- **developer:** Add note: "When a code change causes a regression, use `ens_system_log_search` to find the failing pattern, then `ens_system_log_read` with paging to get context. Validate fix success by re-running the same search."
- **worker:** Add note: "Self-healing use case: if a tool call fails with an opaque error, check the daemon logs before reporting the error back. Often the root cause is in a recent log line."
- **wanderer:** Add note: "Investigation use case: read daemon logs to understand runtime behavior, observe error patterns over time, and gather evidence for diagnostic reports."

**Pre-commit checklist (per `docs/agent-prompt-writing-guide.md`):**
- [ ] `## System Log` section is present and uses the exact 4-tool list above
- [ ] Section is in the cardinal-allowed placement (not buried in agent-specific guidance)
- [ ] Security/redaction note is preserved verbatim
- [ ] Per-agent additions (if any) follow the agent's existing tools_note.md style
- [ ] File passes the 10-section structure check (or is annotated as to why a section is intentionally omitted)

### Task 3: `soul.md` tool inventory updates (BLOCKING — not optional)

Per `docs/agent-prompt-writing-guide.md`, the `soul.md` tool inventory (whether a table, a bulleted list, or an inline enumeration) is the agent's compact reference for "what tools do I have access to." Each of the four agents must include the system-log tools in their inventory.

**For each agent, locate the tool inventory section in `agents/<agent>/soul.md` and add:**

```markdown
### system-log tools
- `ens_system_log_list` — List available log files with sizes and last-modified timestamps
- `ens_system_log_read` — Paged read of log lines with line numbers (offset/limit)
- `ens_system_log_search` — Regex search with context lines and optional level filter
- `ens_system_log_tail` — Read last N lines (tail equivalent) with optional level filter
```

**Format guidance:**
- If `soul.md` uses a table format for tools, add a row per tool with columns matching the existing style.
- If `soul.md` uses a bulleted list grouped by category, add the four bullets under a new `### system-log tools` (or equivalent) heading.
- If `soul.md` uses an inline enumeration in prose, add a parenthetical `(system-log: ens_system_log_list, ens_system_log_read, ens_system_log_search, ens_system_log_tail)` at the appropriate place.

**Per-agent specifics:**

- **leader:** The tool inventory table in `soul.md` already lists categories (instance, self, project, etc.). Add a row for `system-log` with the same column structure.
- **developer:** Existing inventory uses category grouping. Add `### system-log tools` section adjacent to existing `### blueprint tools` or similar.
- **worker:** Existing inventory is similar to developer. Add the four tools under a `system-log` heading.
- **wanderer:** Existing inventory lists `### investigation tools`. Add `### system-log tools` adjacent.

### Task 4: Cross-reference consistency

Verify and update:
- `phase3-plan.md` references to "four tools" (already updated in this revision)
- `phase5-plan.md` test counts (`len(tools) == 4`, expected tool names list includes `ens_system_log_list`)
- Any other document in `.agents/shared/planning/system-log-tools/` that mentions tool counts

## Coupling

- **Loose with:** Phase 3 — the `"system-log"` category string must exist in `CATEGORY_MODULES` for `resolve_tool_filter` to expand it. If Phase 3 is not done, the category silently resolves to nothing (no error, just no tools).
- **Tight with:** `docs/agent-prompt-writing-guide.md` — Tasks 2 and 3 follow its conventions. If the guide is updated, these prompt additions may need to migrate (but the cardinal content — 4 tools, security/redaction note — stays stable).
- **Independent of:** Phases 1, 2, 5

## Risks

- **Silent no-op if Phase 3 incomplete:** If `"system-log"` is in `tools.allow` but not in `CATEGORY_MODULES`, the tools won't appear. This is not an error — `resolve_tool_filter` just skips unknown categories. Mitigation: Phase 3 must be verified before Phase 4.

- **C4 — Prompt updates are BLOCKING.** If `tools_note.md` or `soul.md` updates are skipped, the feature is incomplete per agent prompt conventions. Agents won't know the tools exist from their prompt — they may try to call non-existent tools or miss the security constraints (redaction, path traversal). Mitigation: Tasks 2 and 3 are explicit deliverables with clear acceptance criteria. The pre-commit checklist ensures quality. Reviewer/architect must verify prompt files in PR review.

## Exit Criterion

- All four `meta.json` files have `"system-log"` in their `tools.allow` array
- All four agents have `tools_note.md` with a `## System Log` section documenting all 4 tools
- All four agents' `soul.md` tool inventories include the 4 system-log tools (`ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail`) under the "system-log" category
- After daemon restart, instances of each of the four agents have all 4 `ens_system_log_*` tools in their available tools
- All prompt file changes follow `docs/agent-prompt-writing-guide.md` conventions (10-section structure, pre-commit checklist verified)
- Cross-references in `phase3-plan.md` and `phase5-plan.md` consistently mention "four tools"
