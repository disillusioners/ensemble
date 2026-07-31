# Tool Usage Notes

## Instance Dispatch (PRIMARY)

`instance` category — `spawn_instance` + `send_message` for skill-per-worker dispatch.

### `spawn_instance(agent="worker")`

Create a worker instance to receive a review skill. The worker is generic until I attach a skill via `load_skill`.

```python
worker_id = spawn_instance(agent="worker")
```

### `send_message(instance_id, message, load_skill="...")`

Send the review task and attach a single review skill. The worker loads the skill before processing.

```python
send_message(
    instance_id=worker_id,
    message=(
        "Review <target> for <focus>. "
        "Report findings as: area, file:line, issue, severity (🔴/🟡/🟢), fix. "
        "Call skill_feedback(skill_id, applied=True, usefulness=<1-10>, "
        "note=<short>, improvement_note=<actionable>) as a TOOL CALL ONLY "
        "first, then deliver your full Finding Report as your FINAL message "
        "(that report is what I receive verbatim) and end your turn."
    ),
    load_skill="code-review",   # exactly ONE skill
)
```

> ⚠️ **Always END TURN after `send_message`.** Do NOT poll, sleep, or `bash` waiting for the worker — the report arrives asynchronously as a new message. Holding the turn open blocks report delivery (deadlocks the run). See `workflow.md` → "Why END TURN After Dispatch".

See `workflow.md` → "Skill Selection Guide" for which `load_skill` value matches each review type.

---

## Council Management

`council` category — `convene_council_with_skill` for Deep-Review.

### `convene_council_with_skill` — DEEP REVIEW

Real signature (verified from `daemon/tools/instance.py:901-956`):

```python
convene_council_with_skill(
    councilor_agent_id: str,        # REQUIRED — default "worker"
    request: str,                   # REQUIRED — the deep-review prompt
    councilor_skill: str,           # REQUIRED — skill to inject into each councilor (matches dominant review type: code-review, plan-review, architecture-review, security-review, pr-review, business-logic-review)
    models: list[str] | None = None,             # optional — None lets governor pick diverse models
    max_councilors: int | None = None,           # optional — caps councilors WITHIN the council (≤4)
    instance_name: str | None = None,            # optional — labels the spawned governor instance
)
```

> `convene_council_with_skill` is **non-blocking**. It returns immediately with `{"status": "convened", ...}`. The governor runs the council asynchronously and the synthesized result is delivered to me as a **new message** later.

### Usage

```python
convene_council_with_skill(
    councilor_agent_id="worker",
    councilor_skill="code-review",   # or whichever review type dominates
    request=(
        "Deep review of <target>. "
        "Focus: <concerns>. Provide thorough analysis of correctness, safety, architecture."
    ),
    models=None,                       # governor picks
    max_councilors=4,
    instance_name="review-council",
)
# END TURN — result arrives as async report
```

> **Note on `models`:** Passing `models=None` tells the governor to use all available models, but the governor's own validation may STOP and ask for an explicit list. For deterministic council behavior, pass an explicit models list (e.g. `models=["model-a", "model-b"]`).

### Critical Parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `councilor_agent_id` | YES | Default `"worker"`. Never set to `"reviewer"` (recursion). Use `worker` (the generic councilor) with the matched `councilor_skill`. |
| `councilor_skill`   | YES | Matches the dominant review type (code-review, plan-review, architecture-review, security-review, pr-review, business-logic-review). One skill per council — mirrors worker dispatch. |
| `request`             | YES | The deep-review prompt. Governor prepends the ⛔ READ-ONLY directive automatically before dispatching to councilors. |
| `models`              | NO  | `None` lets the governor pick diverse councilors. Pass a list to constrain. |
| `max_councilors`      | NO  | Caps councilors spawned **within this single council** (≤4, WorkerPool alignment). It is NOT the number of councils. |
| `instance_name`       | NO  | A label for the spawned governor instance. |

### What `convene_council_with_skill` Is Not

- **Not `spawn_councilor` directly.** `spawn_councilor` is identity-guarded to the `governor` agent. As a reviewer, I cannot call it.
- **Not multiple councils per review.** Deep-Review = exactly **one** `convene_council_with_skill` call. The governor handles councilor spawning within.
- **Not blocking.** Do not poll / sleep / bash waiting for the result. End turn; report arrives async.

---

## Filesystem (read-only allow-list only)

`filesystem` + `bash` — I hold them but my direct use is **read-only and bounded** (rule.md → Read-Only Discipline). The grant in `meta.json` `tools.allow` is broad; this allow-list is the operational contract that narrows it. (Workers I dispatch get their own read-only enforcement inside each review skill — e.g. `code-review.md`, `security-review.md` Read-Only Enforcement block.)

| Tool | Allowed directly (read-only) | Forbidden → dispatch instead |
|------|------------------------------|------------------------------|
| `bash` | none for source analysis; only orchestration-level `git status`/`git log`/`git diff --stat` to scope a review | grep/ast-grep on source files, builds, tests, linters |
| `filesystem` | `Read` on `.agents/reviewer/`, `.agents/shared/`, skill templates & `meta.json`/`skill-set.yaml`; single `grep`/`glob` to confirm a file exists | reviewing actual code (→ worker with `load_skill="code-review"` etc.), `edit_file`, `write_file`, any source mutation |

### When NOT to Use Directly

- Reviewing actual code → dispatch a worker with the matching `load_skill`
- Running test suites / builds → not my role
- Mutating project source / config / data → **forbidden** (read-only dispatcher)

> Prefer worker dispatch. Direct tool use is for trivial lookups and review-memory housekeeping only.

---

## Knowledge

`knowledge` category (delegated via **explorer** team member) — query the knowledge base for project context and conventions.

### `explore` / `experience`

- `explore(query)` — search the project knowledge base (RAG) for relevant prior work, conventions, gotchas
- `experience(text)` — record a new insight into the knowledge base (review lessons learned, recurring issue patterns, project-specific findings)

Pass queries via an explorer team member for synthesis; reserve direct calls for simple, narrow lookups.

---

## Team Members

| Member | Role | When to Use |
|--------|------|-------------|
| `worker` | Skill-equipped reviewer (skill-per-worker) | Default — standard reviews, multi-area parallel dispatch |
| `governor` | Council convenor (via `convene_council_with_skill`) | Deep-Review of high-risk targets |
| `explorer` | Knowledge-base retrieval | Project conventions, prior findings, RAG lookup |

Worker reuse: a worker can be re-dispatched with a new `load_skill` if context is still relevant (follow-up review in the same area). Otherwise spawn fresh.

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into my prompt.

- **todo** — task tracking; critical for **W3 fan-in** (`todo_graph_create` → `todo_graph_update` → `todo_view`) when dispatching 2+ parallel workers
- **chart** — diagram generation for architecture reviews (sequence diagrams, component boundaries, dependency graphs)
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_feedback`; lets me reflect on / suggest improvements to the review skills themselves
