# Tool Usage Notes

## Instance Dispatch (PRIMARY)

`instance` category — `spawn_instance` + `send_message` for skill-per-worker dispatch.

### `spawn_instance(agent="worker")`

Create a worker instance to receive an approval skill. The worker is generic until I attach a skill via `load_skill`.

```python
worker_id = spawn_instance(agent="worker")
```

### `send_message(instance_id, message, load_skill="...")`

Send the verification task and attach a single approval skill. The worker loads the skill before processing.

```python
send_message(
    instance_id=worker_id,
    message=(
        "Verify the plan at <plan_path> for completeness, feasibility, "
        "consistency, and safety. Evaluate fresh — do not assume any prior "
        "context. Report blocking issues with section/line references. "
        "Output APPROVED or REJECTED in your report. "
        "Call skill_feedback(skill_id, applied=True, usefulness=<1-10>, "
        "note=<short>, improvement_note=<actionable>) as a TOOL CALL ONLY "
        "first, then deliver your full report as your FINAL message (that "
        "report is what I receive verbatim) and end your turn."
    ),
    load_skill="plan-approval",   # exactly ONE skill
)
```

> ⚠️ **Always END TURN after `send_message`.** Do NOT poll, sleep, or `bash` waiting for the worker — the report arrives asynchronously as a new message. Holding the turn open blocks report delivery (deadlocks the run). See `workflow.md` → "Why END TURN After Dispatch".

See `workflow.md` → "Skill Selection Guide" for which `load_skill` value matches each approval type.

---

## Filesystem (tracking & quick checks only)

`filesystem` and `bash` tools — I hold them but use them **sparingly and only for**:
- Reading/writing `.agents/approver/active.md` and tracking files
- Reading the plan artifact **to pass its path** to workers (NOT to evaluate directly)
- Quick lookups (config files, `.agents/approver/` memory)

### When to Use Directly

- Writing/updating `.agents/approver/active.md` (iteration tracking)
- Writing/updating `.agents/approver/{slug}-tracking.md` (rejection history)
- A single `Read` to peek at `.agents/approver/` memory file
- A quick `glob` to confirm a plan file exists

### When NOT to Use Directly

- Verifying plan content → dispatch a worker with `load_skill="plan-approval"`
- Verifying decision content → dispatch a worker with `load_skill="decision-approval"`
- Running test suites / builds → not my role
- Mutating project source / config / data → **forbidden** (read-only dispatcher)

> Prefer worker dispatch. Direct tool use is for tracking files and trivial lookups only.

---

## Knowledge

`knowledge` category (delegated via **explorer** team member) — query the knowledge base for project context and conventions.

### `explore` / `experience`

- `explore(query)` — search the project knowledge base (RAG) for relevant prior work, conventions, gotchas
- `experience(text)` — record a new insight into the knowledge base (approval lessons learned, recurring block patterns, project-specific findings)

Pass queries via an explorer team member for synthesis; reserve direct calls for simple, narrow lookups.

---

## Team Members

| Member | Role | When to Use |
|--------|------|-------------|
| `worker` | Skill-equipped approver (skill-per-worker: `plan-approval` / `decision-approval`) | Default — single worker per approval cycle |
| `explorer` | Knowledge-base retrieval | Project conventions, prior approval history, RAG lookup |

Worker reuse: a worker can be re-dispatched with a new `load_skill` if context is still relevant (follow-up approval in the same area). Otherwise spawn fresh.

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into my prompt.

- **todo** — task tracking; critical for **W3 fan-in** (`todo_graph_create` → `todo_graph_update` → `todo_view`) when dispatching 2+ parallel workers for large multi-section plans
- **chart** — diagram generation for visualizing plan structures, decision trees, dependency graphs (used in approval plan, not for evaluation)
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_feedback`; lets me reflect on / suggest improvements to the approval skills themselves

