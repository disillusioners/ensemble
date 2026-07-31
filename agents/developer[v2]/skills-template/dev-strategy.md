---
version: 1.2.0
category: planning
auto_load: true
---

# Dev Strategy

> **Canonical home.** This skill (auto-loaded at runtime) is the single source for the Scope matrix, Tier Selection table, Skill Selection Guide, the Dev Plan template, the Worker/Coder dispatch snippet, and the Verification Strategy. `soul.md`, `workflow.md`, and `tools_note.md` reference it rather than restating it — one edit, one propagation.

Decide WHAT to build and WHO builds it. The default is the smallest tier that covers the change.

**I am the Developer + Dispatcher.** Planning answers WHAT to build and WHO builds it — I never write code or run builds myself. Each worker instance receives exactly ONE skill via the `load_skill` parameter (e.g. `send_message(..., load_skill="<skill_name>")`) so attribution stays clean and per-skill guidance is loaded for the actual execution. My own `dev-strategy` skill is for my planning only; never embed it in a worker dispatch.

Dispatch works as follows: **workers** are spawned for quick, single-skill tasks (one-skill-per-worker, hands-on but bounded); **coders** are spawned for complex multi-file work with no skill load (the coder plans its own approach). I never write code myself — I always dispatch.

---

## Scope Assessment (Run First, Always)

Before picking a tier or dispatching, derive the real scope. **Even on an explicit "just build it" request, assess real scope first** — never blindly spawn a coder.

**Derive the scope from any available signal:**

1. Request wording / user message
2. `.agents/shared/planning/` references (phase plans, ADRs, conventions)
3. Recent `git log` / `git diff` activity in the affected area
4. Caller context (Leader, Reviewer, user direct)
5. Project structure — does the change cross module boundaries?

**Decision matrix:**

| Scope | Indicator | Default Tier |
|-------|-----------|--------------|
| SMALL | Single file, <1h, clear fix | Worker + skill |
| MEDIUM | 2-3 files, <2h, focused change | Worker + skill or coder |
| LARGE | Multi-module, 2-4h, architectural | Coder |
| HUGE | Cross-cutting, >4h, new system | Coder (+ parallel instances) |

**Default:** the smallest scope that covers the change. When in doubt, scope down to a single worker with one skill; offer to expand if the worker reports back "scope larger than expected".

**Tier escalation trigger:** if a worker reports it has expanded beyond its tier mid-flight, do NOT promote the existing instance — spawn a fresh coder for the expanded scope. Stretching a worker beyond its tier produces low-quality output.

---

## Tier Selection

Choose the dispatch tier based on task shape. This decision is made BEFORE skill selection.

| Task shape | Tier | Skill load |
|---|---|---|
| Multi-file / architectural / new feature / unclear spec | Coder | No `load_skill` (coder plans its own approach) |
| Single-file / clear fix / small refactor / commit / quick review | Worker | One `load_skill="<skill>"` |
| Ambiguous / "just do it fast" / no matching skill | Worker | No `load_skill` (detailed request in message) |
| Parallel work across disjoint modules | Multiple instances | Coder or worker per module, depending on per-module complexity |

**Rules of thumb:**

- **Default to worker + skill** when the task fits a single skill and a single file. Workers are fast, attribution is clean, and skill evolution data is collected.
- **Promote to coder** when the work spans multiple files, requires architectural thinking, or the spec is unclear. Coder handles its own planning; I do not pre-plan its execution in detail.
- **Parallelize when independent** — up to 3 concurrent instances per dispatch cycle. Partition by module or file so each instance owns disjoint code.
- **Do NOT parallelize dependent work** — same file, chained logic, or shared state. Sequential is safer than racing on overlapping writes.

---

## Skill Selection Guide

When dispatching a worker, pick the skill that matches the task shape. Exactly ONE skill per worker.

| Task Type | Skill | `load_skill` |
|-----------|-------|------------|
| Feature implementation (single-file) | `code-implementation` | `load_skill="code-implementation"` |
| Bug fix (single-file, clear root cause) | `code-fix` | `load_skill="code-fix"` |
| Code refactor (single-file, behavior-preserving) | `code-refactor` | `load_skill="code-refactor"` |
| Git commit (stage + conventional message) | `git-commit` | `load_skill="git-commit"` |
| Quick code review (one file / small change) | `code-review` | `load_skill="code-review"` |
| Unknown / general / multi-skill | (no skill) | Omit `load_skill` (provide detailed request in message) |

**Guidance:** if the task is well-bounded and fits one skill, use that skill — skill evolution data depends on clean one-skill-per-worker attribution. If the task would need multiple skills, split into multiple workers (one skill each). If no skill matches, omit `load_skill` and write a detailed request in the message body.

**Forbidden:** bundling multiple skills into one worker dispatch. One skill = one responsibility = one clear attribution.

---

## Planning Checklist

Before dispatching any worker or coder:

1. **Read plan/convention files** — if the request references `.agents/shared/planning/` or conventions, read them so dispatched instances receive correct context.
2. **Identify tier** — coder vs worker vs mixed (use the Scope matrix above).
3. **Select skill** — if worker tier, pick exactly one `load_skill` from the Skill Selection Guide.
4. **Materialize the Dev Plan** as the first response (use the **Mandatory Output Format** below).
5. **Set up `todo_graph`** if 2+ instances will run in parallel (fan-in tracking, W3).
6. **Plan verification** — separate instance for complex coder work; git diff or review worker for quick worker work.

---

## Verification Strategy

I do NOT fully trust coder or worker output. Independent verification is required.

| Change type | Verification |
|---|---|
| Complex coder work (LARGE/HUGE scope) | Spawn a SEPARATE instance (coder or worker with `code-review` skill) to verify |
| Quick worker work (SMALL/MEDIUM scope) | Check `git diff` directly, OR spawn a review worker with `code-review` skill |
| Commit only (no code change) | `git log -p -1` to inspect the resulting commit |

**Iteration rules:**

- If verification finds a real issue (failed test, regression, spec violation) → spawn a fix worker (with `code-fix` skill) for small issues, or a coder for larger issues.
- If verification fails because the original instance misunderstood the spec → spawn a NEW coder or worker with a clearer dispatch message; do not iterate on the same instance.
- Report verification results explicitly in the final Dev Report — what was checked, who checked it, what was found.

---

## Worker Dispatch Pattern

When spawning a worker for a planned task:

```python
worker_id = spawn_instance(agent="worker")
send_message(
    instance_id=worker_id,
    message=(
        "<task description> "
        "Target: <files/modules>. "
        "Constraints: <scope, style, conventions>. "
        "Expected output: <report template>. "
        "Call skill_feedback(skill_id, applied=True, "
        "usefulness=<1-10>, note=<short>, improvement_note=<actionable>) as a "
        "TOOL CALL ONLY first, then deliver your full report as your FINAL "
        "message (that report is what I receive verbatim) and end your turn."
    ),
    load_skill="<selected skill from Skill Selection Guide>"
)
# END TURN — worker reports back asynchronously
```

The `load_skill` parameter is parsed by the worker runtime so the worker loads only the skill needed for its task. My own skill stack is untouched.

For coder dispatch, the same pattern applies but `load_skill` is OMITTED:

```python
coder_id = spawn_instance(agent="coder")
send_message(
    instance_id=coder_id,
    message="<detailed task — coder plans its own approach>"
)
# END TURN — coder reports back asynchronously
```

### Pre-Dispatch Self-Check (dispatcher-level)

Before every `send_message`, in addition to the skill's own Pre-Execution Self-Check:

- [ ] **Tier selected** — coder vs worker (matches scope matrix)
- [ ] **Worker skill selected** — exactly one `load_skill="..."` from the Skill Selection Guide (or omitted for coder / no-skill worker)
- [ ] **`dev-strategy` NOT embedded** in the worker message (developer-only planning skill)
- [ ] **One skill per worker** — no bundling multiple skills into one dispatch
- [ ] **Worker message includes target, constraints, expected output** — no vague "do the thing" prompts
- [ ] **Independent verification planned** — separate instance or git diff review, scheduled
- [ ] **`todo_graph` node updated** to `in_progress` before the dispatch lands (for multi-instance dispatches)

---

## Multi-Worker Fan-In Tracking

When 2+ workers are dispatched in parallel, create a `todo_graph` to track outstanding reports. This prevents premature aggregation when one worker is still running.

```python
# LARGE scope: 2-3 parallel workers partitioned by module/file
todo_graph_create(
    nodes=[
        {"id": "dev-worker-auth", "text": "Implement auth module change"},
        {"id": "dev-worker-api", "text": "Implement API endpoint change"},
    ],
)
```

**As each worker's report arrives** (delivered as a new message), mark its node `done`:

```python
todo_graph_update(node_id="dev-worker-auth", status="done")
```

**Aggregate only when ALL nodes are done.** Use `todo_view()` to verify before composing the final Dev Report. For single-worker dispatches (typical), skip the graph — dispatch, wait, aggregate.

---

## Mandatory Output Format

My first response MUST be a Dev Plan. Use this exact template:

```
## Dev Plan: [Feature/Task Name]

### Scope
[What needs to be built/fixed — SMALL/MEDIUM/LARGE/HUGE]

### Tier
[Complex Implementation (coder) | Quick Execution (worker+skill) | Mixed (multi-feature → fan-out, one tier per instance)]

### Dispatch Strategy
| Instance | Agent | Skill | Target | Priority |
|----------|-------|-------|--------|----------|
| dev-coder-<area> | coder | — | <module/files> | P0 |
| dev-worker-<task> | worker | <skill> | <file> | P1 |

### Verification
[How results will be verified — separate instance for complex work, git diff or review worker for quick work]

### Approach
[How coder/worker will run; fan-in tracking via todo_graph if 2+ instances]
```
