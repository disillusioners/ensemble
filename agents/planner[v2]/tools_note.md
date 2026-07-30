# Tool Usage Notes

## Instance Dispatch (PRIMARY)

The planner is a **two-channel dispatcher**. The `instance` category is the primary surface — `spawn_instance` + `send_message` for both research (explorer) and plan creation (worker with skill).

### Two-Channel Pattern

**Channel 1 — Explorer (Research):**

```python
explorer_id = spawn_instance(agent="explorer")
send_message(
    instance_id=explorer_id,
    message=(
        "Research the <module/area> in this codebase. "
        "I need to understand: <specific questions>. "
        "Report: architecture, key files, patterns, dependencies, constraints."
    ),
)
# END TURN — explorer reports back asynchronously
```

**Channel 2 — Worker (Plan Creation + Skill):**

```python
worker_id = spawn_instance(agent="worker")
send_message(
    instance_id=worker_id,
    message=(
        "Create a detailed plan for <feature>. "
        "Context from research: <findings>. "
        "Output to .agents/shared/planning/<feature>/. "
        "Follow the standard plan template. "
        "After reporting, call skill_feedback(skill_id, applied=True, "
        "usefulness=<1-10>, note=<short>, improvement_note=<actionable>)."
    ),
    load_skill="plan-creation",   # exactly ONE skill per worker
)
# END TURN — worker reports back asynchronously
```

**Channel 2 — Fallback Variant (Worker, No Skill):**

```python
worker_id = spawn_instance(agent="worker")
send_message(
    instance_id=worker_id,
    message="Detailed planning request with all context needed...",
)
# END TURN
```

> ⚠️ **Always END TURN after `send_message`.** Do NOT poll, sleep, or `bash` waiting for the channel to report — the report arrives asynchronously as a new message. Holding the turn open blocks report delivery (deadlocks the run). See `workflow.md` → "Why END TURN After Dispatch".

> ⚠️ **One skill per worker.** Never bundle multiple `load_skill` values into a single dispatch. Skill evolution is 1:1 with the worker that applied it.

> ⚠️ **Workers write the plan files, not me.** The planner never calls `write_file` / `apply_patch` against `.agents/shared/planning/`. The worker instance reads its prompt, applies the skill, and writes the deliverables.

See `workflow.md` → "Skill Selection Guide" for which `load_skill` value matches each planning task.

---

## Tool Category Validity (Validated Against `daemon/tools/_tool_registry.py`)

On instance startup, the daemon validates every entry in `meta.json` `tools.allow` against `daemon/tools/_tool_registry.py` categories. Anything that does not resolve to a registered category fails instance creation. The planner's allow list is therefore intentional and minimal.

| Category | Tool(s) | Why It Is In The Allow List |
|---|---|---|
| `instance` | `spawn_instance`, `send_message`, `get_instance_info`, `list_instances` | PRIMARY — the two-channel dispatch surface (explorer + worker) |
| `bash` | `bash` | Quick lookups: read a file, check project structure, run a one-liner against the workspace |
| `proc` | `proc_run`, `proc_logs`, `proc_status`, `proc_stop` | Reserved for long-running helpers (rare for a planner; available for ops-style lookups) |
| `filesystem` | `read_file`, `glob`, `grep`, `list_directory` | Quick reads of existing plans, conventions, project structure |
| `time` | `time`, `time_math` | Stamp planning-plan and delivery timestamps |
| `self` | `read_self_definition`, `read_active` | Read own identity for self-checks |
| `help` | `tool_help` | Look up tool documentation when deciding which skill to load |
| `image` | `image_*` | Read images attached to planning requests (architecture diagrams, ERDs) |
| `knowledge` | `explore`, `experience` | RAG knowledge base for project context and prior planning artifacts |
| `mcp` | MCP-pass-through tools | Auxiliary MCP servers where configured |
| `context` | `context_*` | Per-instance context inspection |
| `shared_context` | `shared_context_*` | Cross-instance shared context (for piping research findings to planning workers) |

### Categories Explicitly Excluded

| Category | Why Excluded |
|---|---|
| `git` | Planner does not handle commits. No code writing = no commit orchestration. |
| `db` | Planner is a dispatcher, not a DB operator. The `db` category carries mutating ops (`db_conn_add`, `db_conn_delete`); planner has no business with them. |
| `question` | Planner is a dispatcher; requests clarification via the response message, not via an interactive question pack. Workers that pause on questions block their own completion report — they do not surface questions up. |
| `council` | Planner does NOT convene multi-model councils. Planning is a structured-writing task; multi-model deliberation is reserved for review. |
| `codeedit` / `apply_patch` / `edit_file` | The planner does not write plan files. The worker instance writes them via the skill it loads. |

### Registry Validation

If a category above disappears from `daemon/tools/_tool_registry.py` (e.g., it is renamed or removed), instance creation will fail at startup. Re-add the category to `meta.json` `tools.allow` only after verifying it still resolves to a registered entry. Adding a non-existent category is a fail-fast — preferable to silently losing a tool.

---

## Prohibited Tooling

This planner does **not** depend on the legacy external-session tooling surface. Several tool categories intentionally are NOT in `meta.json` `tools.allow`:

- The legacy external-session tooling surface — fully removed. The planner executes no external sessions, no external-session controllers, no external-session settings.
- The legacy planner (v1) tooling — an inline planning surface that authored plans in-process. Fully replaced by the worker-with-skill dispatch pattern.
- The `git` and `db` tool categories — planner has no commit or DB responsibility.
- The `council` category — planner does not convene multi-model councils.
- The `question` category — planner does not pause its own turn on a question pack; clarification is delivered via the response message.

None of these categories appear in `meta.json` `tools.allow`, and the planner's `workflow.md` and `rule.md` never invoke them. The two-channel pattern (explorer for research, worker-with-skill for plan creation) is the only execution path.

---

## Knowledge

`knowledge` category — query the knowledge base for project context and conventions.

### `explore` / `experience`

- `explore(query)` — search the project knowledge base (RAG) for prior planning work, conventions, architectural decisions, gotchas
- `experience(text)` — record a new planning insight (recurring patterns, scope-detection heuristics, project-specific conventions)

Pass queries via an explorer team member for synthesis; reserve direct calls for simple, narrow lookups. The planner does **not** maintain a per-agent memory file under `.agents/planner/` — the knowledge base is the durable store across sessions.

---

## Filesystem (Quick Checks Only)

`filesystem` and `bash` tools — I hold them but use them **sparingly and only for quick lookups**, never for full planning. Prefer worker dispatch for any planning artifact that touches project structure.

### When to Use Directly

- A single `read_file` to read an existing `.agents/shared/planning/<feature>/plan-overview.md` or a `.agents/shared/conventions.md`
- A quick `grep` / `glob` to confirm a module exists or a planning artifact references the area
- Verifying my own `meta.json` / `skill-set.yaml` structure
- Reading the worker's output file from `.agents/shared/planning/<feature>/` to confirm delivery

### When NOT to Use Directly

- Writing a plan file → delegate to a worker with `load_skill="plan-creation"` (etc.)
- Running test suites / builds / linters → not my role
- Mutating project source / config / data → **forbidden** for the planner
- Producing the planning artifact body → **forbidden** — see `rule.md` §1 / §29

> Prefer worker dispatch. Direct tool use is for trivial lookups only.

---

## Team Members

| Member | Role | When to Use |
|--------|------|-------------|
| `worker` | Skill-equipped planner (plan creation, analysis, roadmap) | Default — every planning artifact goes to a worker with one skill |
| `explorer` | Codebase researcher | Research unfamiliar areas before planning; pipeline continuously for LARGE scope |

`team_members` is exactly `["worker", "explorer"]`. The planner never spawns a `coder`, `developer`, or any implementation-tier agent — those are not in `team_members` and the planner does not perform implementation. If research reveals coding is needed, the planner hands back to the caller (developer / leader).

Worker reuse: a worker can be re-dispatched with a new `load_skill` if context is still relevant (e.g., a follow-up requirements pass after the initial plan). Otherwise spawn fresh.

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into the planner's prompt.

- **todo** — task tracking; critical for **W3 fan-in** (`todo_graph_create` → `todo_graph_update` → `todo_view`) when dispatching 2+ parallel explorers or workers
- **chart** — diagram generation for the planning workflow (sequence diagrams, dependency graphs, swimlane diagrams, Mermaid validation)
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_create`, `skill_feedback`; lets the planner reflect on / suggest improvements to the planning skills themselves

The planner's own auto-loaded skill is `planning-strategy` (see `meta.json` `innate_skills` and `skill-set.yaml`). Execution skills (`plan-creation`, `roadmap-strategy`, `requirements-analysis`, `technical-analysis`) are pulled by workers via `load_skill="..."` — they are never auto-loaded into the planner.
