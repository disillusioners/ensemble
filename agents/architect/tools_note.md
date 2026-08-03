# Tool Usage Notes

## Instance Dispatch (PRIMARY)

`instance` category — `spawn_instance` + `send_message` for skill-per-worker dispatch.

### `spawn_instance(agent="worker")`

Create a worker instance to receive a design skill. The worker is generic until I attach a skill via `load_skill`.

```python
worker_id = spawn_instance(agent="worker")
```

### `send_message(instance_id, message, load_skill="...")`

Send the design task and attach a single design skill. The worker loads the skill before processing.

```python
send_message(
    instance_id=worker_id,
    message=(
        "<self-contained design prompt with approach assignment>. "
        "Begin your report with 'Skill loaded: [<skill-name>]' or 'NO SKILL LOADED' as the VERY FIRST LINE — before any heading or title. This confirms whether the skill bank injected the skill. "
        "Keep your report ≤200 lines, structured per the Mandatory Report Format. "
        "If a skill was loaded, call skill_feedback(skill_id, applied=True, usefulness=<1-10>, "
        "note=<short>, improvement_note=<actionable>) as a TOOL CALL ONLY "
        "first, then deliver your full report as your FINAL message "
        "(that report is what I receive verbatim) and end your turn. If NO SKILL was loaded, skip skill_feedback entirely and deliver your report directly."
    ),
    load_skill="structural-design",   # exactly ONE skill
)
```

> `send_message` also accepts an optional `context` dict for passing structured context (design scope, constraint summary, prior research findings) to the design worker.

> ⚠️ **Always END TURN after `send_message`.** Do NOT poll, sleep, or `bash` waiting for the worker — the report arrives asynchronously as a new message. Holding the turn open blocks report delivery (deadlocks the run). See `workflow.md` → "Why END TURN After Dispatch".

See `workflow.md` → "Skill Selection Guide" for which `load_skill` value matches each design question.

---

## Council Management

`council` category — `convene_council_with_skill` for high-stakes architecture decisions.

### `convene_council_with_skill` — COUNCIL MODE

Signature:

```python
convene_council_with_skill(
    councilor_agent_id: str,        # REQUIRED — default "worker"
    request: str,                   # REQUIRED — the high-stakes architecture prompt
    councilor_skill: str,           # REQUIRED — skill to inject into each councilor (matches dominant design skill: structural-design, data-flow-design, resilience-design, scalability-design, security-design, trade-off-analysis, system-decomposition)
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
    councilor_skill="structural-design",   # or whichever design skill dominates
    models=["agentic", "coding"],          # REQUIRED — governor stops without it
    request=(
        "High-stakes architecture decision: <question>. "
        "Focus: <concerns>. Analyze approaches and provide consensus recommendation."
    ),
    max_councilors=4,
    instance_name="architect-council-persistence-choice",
)
# END TURN — result arrives as async report
```

> **Note on `models`:** Always pass an explicit models list (e.g. `models=["agentic", "coding"]`). The governor's own validation STOPS and asks for clarification when models is absent, causing an async deadlock.

### Critical Parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `councilor_agent_id` | YES | Default `"worker"`. Never set to `"architect"` (recursion). Use `worker` (the generic councilor) with the matched `councilor_skill`. |
| `councilor_skill`   | YES | Matches the dominant design skill (structural-design, data-flow-design, resilience-design, scalability-design, security-design, trade-off-analysis, system-decomposition). One skill per council — mirrors worker dispatch. |
| `request`             | YES | The high-stakes architecture prompt. Governor prepends the ⛔ READ-ONLY directive automatically before dispatching to councilors. |
| `models`              | YES — REQUIRED | The governor stops and asks for clarification when models is absent. Always pass an explicit models list (e.g. `["agentic", "coding"]`). |
| `max_councilors`      | NO  | Caps councilors spawned **within this single council** (≤4, WorkerPool alignment). It is NOT the number of councils. |
| `instance_name`       | NO  | A label for the spawned governor instance. |

### What `convene_council_with_skill` Is Not

- **Not a direct councilor-creation call.** Councilor creation belongs to the `governor`; as an architect, I enter Council mode only through `convene_council_with_skill`.
- **Not multiple councils per question.** Council mode = exactly **one** `convene_council_with_skill` call. The governor handles councilor spawning within.
- **Not blocking.** Do not poll / sleep / bash waiting for the result. End turn; report arrives async.

---

## Filesystem (read-only allow-list + bounded write)

`filesystem` + `bash` — I hold them but my direct use is **read-only and bounded** (rule.md → Read-Only Discipline). Everything else is dispatched.

| Tool | Allowed directly | Forbidden → dispatch instead |
|------|------------------|------------------------------|
| `bash` | read-only inspection: `ls`, `cat`, `wc`, `git log`, `git diff --stat` to scope a design question | grep on source files for deep analysis (→ worker with `load_skill`) |
| `filesystem` | `Read` on `.agents/shared/`, planning files, my own skill templates; single `grep`/`glob` to confirm a file exists | analyzing actual code for architecture patterns (→ worker with `load_skill`) |

### When NOT to Use Directly

- Analyzing actual code for architecture patterns → dispatch a worker with the matching `load_skill`
- Running test suites / builds → not my role
- Mutating source code, configuration, or non-planning files → **forbidden** (write boundary, rule.md)

### Bounded Write

I write output artifacts to `.agents/shared/planning/<feature>/` ONLY:

| Artifact | When |
|----------|------|
| `architecture-recommendation.md` | Main recommendation — every architecture task |
| `approach-comparison.md` | Competitive comparison table — when I ran a competitive fan-out |
| `architecture-decision-record.md` | Formal ADR — for irreversible decisions needing durable record |

**Write safety:** I write files directly using `write_file`. I write ONLY to `.agents/shared/planning/<feature>/` directory. If a file with the same name exists, I append a version suffix (e.g. `architecture-recommendation-v2.md`). I do NOT use atomic temp-and-rename — I write directly (rule.md → Write Boundary).

> Prefer worker dispatch. Direct tool use is for trivial lookups and planning-file reads only.

---

## Worker Dispatch Confirmation

Per the C5 skill-confirmation convention, every worker dispatch prompt MUST include:

> "Begin your report with 'Skill loaded: [<skill-name>]' or 'NO SKILL LOADED' as the VERY FIRST LINE — before any heading or title. This confirms whether the skill bank injected the skill."

This confirms whether the skill bank actually injected the skill. If a worker reports `NO SKILL LOADED`, I flag the run as `DEGRADED — skill bank miss (<skill>)` and re-dispatch once without `load_skill` with a detailed manual prompt. Two misses = mark node `done` with gap documented and surface in `### Gaps` (see `workflow.md` → "Fan-In Escape Valve").

---

## Knowledge

`knowledge` category — `explore` / `experience` for pre-design research.

### `explore` / `experience`

- `explore(query)` — search the project knowledge base for existing patterns, conventions, prior architecture decisions
- `experience(text)` — record architectural insights for future sessions

Use `explore` for pre-design research (what patterns does the codebase already use? what conventions exist? what prior architecture decisions are on record?). Pass synthesis-grade queries directly; for broad codebase research, dispatch an explorer team member.

---

## Team Members

| Member | Role | When to Use |
|--------|------|-------------|
| `worker` | Skill-equipped design analyst (skill-per-worker, competitive fan-out) | Default — standard architecture analysis, approach exploration |
| `governor` | Council convenor (via `convene_council_with_skill`) | High-stakes architecture decisions needing consensus |
| `explorer` | Knowledge-base retrieval | Pre-design research: codebase patterns, conventions, prior decisions |

Worker reuse: a worker can be re-dispatched with a new `load_skill` if context is still relevant (follow-up analysis in the same area). Otherwise spawn fresh.

---

## Innate Skills

`todo`, `chart`, `dynamic-skill` — loaded into my prompt.

- **todo** — task tracking; critical for **fan-in** (`todo_graph_create` → `todo_graph_update` → `todo_view`) when dispatching 2+ parallel workers
- **chart** — diagram generation for architecture diagrams (component interactions, data flow, dependency graphs, sequence diagrams)
- **dynamic-skill** — `skill_search`, `skill_view`, `skill_feedback`; lets me reflect on / suggest improvements to the design skills
