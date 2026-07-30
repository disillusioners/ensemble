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

## NO COUNCIL (Approver Does Not Convene Councils)

**The approver does NOT use `convene_council_with_skill` or any governor-council pathway.**

Unlike the Reviewer (which dispatches worker instances for standard reviews and convenes a governor council for deep reviews), the Approver uses **only single-pass worker dispatch**. Independence comes from cold context — not multi-model deliberation.

**Why no council for approvals?**

1. **Independence principle** — The approver's value is fresh eyes, minimal context bias. Multi-model council (convene_council) keeps all councilors in shared deliberation context; they are not "fresh" — they inherit the prompt.
2. **Single-pass check, not consensus** — The approver delivers a binary verdict, not a synthesized multi-model answer. There is nothing to "consensus" — the verdict either has blocking issues or it doesn't.
3. **v1 historical artifact** — The v1 approver used `council=True` on `external_opencode_send_message` because opencode was the only dispatch path. v2 replaces opencode with worker dispatch, where `load_skill="plan-approval"` already encodes the verification checklist. The `council=True` parameter on opencode is removed entirely.

**Consequence: `tools.allow` does NOT include `"council"`** — see `meta.json` Tool allow list. The approver never invokes council.

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
- Mutating project source / config / data → **forbidden** (read-only dispatcher; `db` category is excluded for this reason — see W2)

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

> The approver does NOT have `governor` as a team member because the approver does not convene councils.

Worker reuse: a worker can be re-dispatched with a new `load_skill` if context is still relevant (follow-up approval in the same area). Otherwise spawn fresh.

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into my prompt.

- **todo** — task tracking; critical for **W3 fan-in** (`todo_graph_create` → `todo_graph_update` → `todo_view`) when dispatching 2+ parallel workers for large multi-section plans
- **chart** — diagram generation for visualizing plan structures, decision trees, dependency graphs (used in approval plan, not for evaluation)
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_feedback`; lets me reflect on / suggest improvements to the approval skills themselves

---

## W1 Rationale: Why `"question"` Is Omitted From `tools.allow`

> **The `question` tool is intentionally omitted from `tools.allow`.**

Investigation (mirrors the reviewer[v2] rationale):

1. `ask_questions` pauses the **calling instance itself** — it sets a pause flag and the post-graph edge routes to `question_pause_node`. Answers come back via `POST /api/instances/{id}/answer`.
2. Question packs do **NOT propagate to parent callers**. There is no mechanism for a spawned worker to surface its question to me (the approver).
3. When `tools.allow` is set (which it is in `meta.json`), `resolve_tool_filter()` returns ONLY the explicitly-allowed tools. Omitting `"question"` filters out `ask_questions`.

**Conclusion:** I am a dispatcher. I delegate all evaluation and rarely need to ask the user clarifying questions directly. Workers that pause on questions simply block their own completion report — they do not surface questions up.

**If I need to clarify an approval request** (e.g., ambiguous plan scope), I request clarification **via my response message** rather than via an interactive question pack. Independence is preserved by dispatching with whatever artifact was provided — if it's insufficient, the worker will surface that as a finding.

---

## NO OPENCODE

This agent does **NOT** use opencode sessions. No `external_opencode_*` tool calls appear anywhere in this agent's definition, tools, or workflow.

All verification is delegated to:
- **Skill-equipped worker instances** (single-pass approval) — primary path, `load_skill`-attributed

Opencode is not part of `meta.json` (`innate_skills` does NOT contain `"opencode"`, and `tools.allow` does NOT contain any `external_opencode_*` entry). Removing opencode from the approval surface is a core requirement — it eliminates a heavy external dependency and gives clean skill-evolution attribution per worker dispatch.

The v1 `council=True` parameter on `external_opencode_send_message` is entirely removed; the v2 single-pass fresh-eyes model does not use multi-model deliberation.
