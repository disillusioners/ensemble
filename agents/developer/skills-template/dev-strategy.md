---
version: 1.4.0
category: planning
auto_load: true
---

# Dev Strategy

> **Canonical home.** This skill (auto-loaded at runtime) is the single source for the Scope matrix, Tier Selection table, Skill Selection Guide, the Dev Plan template, the Worker/Coder dispatch snippet, and the Verification Strategy. My soul, workflow, and tools files reference it rather than restating it — one edit, one propagation.

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
| Single-file / clear fix / small refactor / commit | Worker | One `load_skill="<skill>"` |
| Ambiguous / "just do it fast" / no matching skill / quick review (no `code-review` skill exists) | Worker | No `load_skill` (detailed request in message) |
| Parallel work across disjoint modules | Multiple instances | Coder or worker per module, depending on per-module complexity |

**Rules of thumb:**

- **Default to worker + skill** when the task fits a single skill and a single file. Workers are fast, attribution is clean, and skill evolution data is collected.
- **Promote to coder** when the work spans multiple files, requires architectural thinking, or the spec is unclear. Coder handles its own planning; I do not pre-plan its execution in detail.
- **Parallelize when independent** — up to 3 concurrent instances per dispatch cycle. Partition by module or file so each instance owns disjoint code.
- **Do NOT parallelize dependent work** — same file, chained logic, or shared state. Sequential is safer than racing on overlapping writes.

---

## Skill Selection Guide

When dispatching a worker, pick the skill that matches the task shape. Exactly ONE skill per worker.

| Task Type | Skill (the `load_skill` value) | Bank status (verified 2026-10-20) | Fallback if absent at dispatch time |
|-----------|--------------------------------|----------------------------------|------------------------------------|
| Feature implementation (single-file) | `code-implementation` | ✅ present | — |
| Bug fix (single-file, clear root cause) | `code-fix` | ✅ present (A/B variant — still servable) | — |
| Code refactor (single-file, behavior-preserving) | `code-refactor` | ✅ present | — |
| Git commit (stage + conventional message) | `git-commit` | ✅ present | — |
| Quick code review (one file / small change) | `code-review` — **DOES NOT EXIST in the skill bank** | ❌ absent | **Guideline #19 – Skill-bank fallback:** dispatch a worker/coder with NO `load_skill` and a manual-review checklist prompt; flag `DEGRADED — skill bank miss (code-review)` |
| Unknown / general / multi-skill | (none) | n/a | — |

**The bank is live — verify before you rely on it.** The "Bank status" column is a snapshot, not a promise: skills get captured, A/B-tested, and deactivated. Before dispatching with a `load_skill` value, confirm the skill actually exists (`skill_search` / `skill_list`). If it is missing, use the fallback column — within-tier manual review plus a `DEGRADED — skill bank miss (<name>)` flag (Guideline #19) — never dispatch a skill-less worker silently.

**`load_skill` rides on `send_message`, NOT on `spawn_instance`.** `spawn_instance` has no `load_skill` parameter, and `spawn_instance(..., task=...)` auto-dispatch CANNOT carry a skill. To attach a skill to a fresh worker, use the spawn→send ritual: `spawn_instance(agent="worker")` (no `task`), then `send_message(..., load_skill="<skill>")`. A send bearing `load_skill` always routes through the enqueue pipeline, requires the target instance to be idle, and is never flattened into a live turn.

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
6. **Plan verification** — minimal & scoped (Cardinal #6): one targeted test or smoke for the touched code (≤2-min cap), or a manual-review diff pass — there is no `code-review` skill in the bank, so use the Guideline #19 within-tier fallback. Full/regression testing is DEFERRED to the tester agent — note it in the plan's Verification line.

---

## Verification Strategy

**Principle: minimal and scoped.** The dedicated **tester** agent owns full/regression/integration testing in the bigger workflow. My verification only proves the *dispatched change* didn't obviously break — it does **not** re-run the project's test suite. *(Cardinal #6 – Minimal verification; Verification Scope #20–#24.)*

### Step 1 — Derive the change set (before verifying)
From `git diff --stat` (read-only, #14) and the worker/coder report, name the exact files/functions touched. Verification scopes to **that set** — nothing wider. Never "discover and run" tests beyond the change set.

### Step 2 — Pick the smallest check that covers the change (one, in order)

| Change type | Verification | Cap |
|---|---|---|
| Complex coder work (LARGE/HUGE) | Spawn a SEPARATE instance to **review the diff** (manual-review prompt — no `code-review` skill exists; Guideline #19 fallback) + the one targeted test below | review + 1 targeted test |
| Quick worker work (SMALL/MEDIUM) | `git diff` directly, OR spawn a review worker with a manual-review prompt (no `load_skill`; Guideline #19 fallback) | diff / review |
| Commit only (no code change) | `git log -p -1` to inspect the resulting commit | — |

The "one targeted test" = a single test for the changed unit, e.g. `pytest path/to/test_changed.py::test_name -q`. **Bounded.** Fallbacks when no targeted test fits: a fast smoke (`python -c "import …"`, `cd frontend && tsc --noEmit`, `ruff check <file>`) or a manual-review diff pass with **no execution** (Guideline #19 fallback).

### Step 3 — Timeout cap every run
Unit ≤2 min; smoke ≤1 min. A verify worker never "discovers and run" extra tests. If the targeted test won't finish in cap → it's the wrong (too big) check: narrow further, or record `DEFERRED → tester` (the caller escalates — I do not spawn it; `tester` is not in my `team_members`).

### Forbidden here (tester's job)
- `pytest tests/`, `pytest tests/ -x`, `go test ./...`, whole-suite / regression / "run all tests" — **never** run or dispatch, by me or a coder/worker.
- If the smallest check is green but I'm tempted to "run the whole suite to be safe" → STOP. That's the tester's signal. Record `Regression/full testing: DEFERRED → tester` in the Dev Report `### Remaining` and finish.

### Iteration rules
- If verification finds a real issue (failed targeted test, regression in the touched code, spec violation) → spawn a fix worker (`code-fix`) for small issues, or a coder for larger. Cap at **3 iterations** (#17); beyond that report `Partial`, name the failing test/issue.
- If verification fails because the original instance misunderstood the spec → spawn a NEW coder/worker with a clearer dispatch message; do not iterate on the same instance.
- **Report the scope decision** in the Dev Report `### Verification`: the change set, the single check run (+ cap), and the explicit `DEFERRED → tester` line for full/regression coverage.

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
        "Before ending any turn: begin work with a tool call, deliver your "
        "report, or report open questions (unless you are the user-facing root) "
        "— a turn that ends on future-intent text with zero "
        "tool calls is treated as a junk report. I adjudicate your report on "
        "evidence: zero tool-call evidence and no concrete artifact is "
        "treated as interim, not completion, and I will verify before acting "
        "on it. "
        "Call skill_feedback(skill_id, applied=True, "
        "usefulness=<1-10>, note=<short>, improvement_note=<actionable>) as a "
        "TOOL CALL ONLY first, then deliver your full report as your FINAL "
        "message (that report is what I receive verbatim) and end your turn."
    ),
    load_skill="<selected skill from Skill Selection Guide>"
)
# END TURN — worker reports back asynchronously
```

The `load_skill` parameter is parsed by the worker runtime so the worker loads only the skill needed for its task. My own skill stack is untouched. **The spawn→send sequence above is the only way to attach a skill to a fresh worker** — `spawn_instance` has no `load_skill` parameter, and its `task=...` auto-dispatch cannot carry one. A `load_skill` send enqueues (the target must be idle) and is never flattened into a live turn.

For coder dispatch, the same pattern applies but `load_skill` is OMITTED:

```python
coder_id = spawn_instance(agent="coder")
send_message(
    instance_id=coder_id,
    message="<detailed task — coder plans its own approach>"
)
# END TURN — coder reports back asynchronously
```

### Passing Task Context (optional)

- I may pass a `context={...}` dict on `send_message(...)` to hand a worker or coder supplementary info beyond the task message — file paths, investigation findings, root-cause notes, plan/convention references.
- **USE when I have:**
  - Specific files or locations the child should focus on
  - Findings or a root cause from my own verification
  - A `.agents/shared/planning/` or conventions doc to reference
- **SKIP when:** simple commit prompts, quick single-file fixes where the message already carries everything, or control messages.
- **Suggested keys:** `files` (list), `notes` (str), `plan_ref` (str). Any key passes through.
- Don't duplicate what's already in the message text — `context` is for supplementary info only.

```python
send_message(
    instance_id=worker_id,
    message="...",
    load_skill="code-implementation",
    context={
        "files": ["src/middleware/auth.py:42-58"],
        "notes": "The refresh_token rotation skips the cache invalidation.",
        "plan_ref": ".agents/shared/planning/fix-auth/phase1.md",
    },
)
```

### Pre-Dispatch Self-Check (dispatcher-level)

Before every `send_message`, in addition to the skill's own Pre-Execution Self-Check:

- [ ] **Tier selected** — coder vs worker (matches scope matrix)
- [ ] **Worker skill selected** — exactly one `load_skill="..."` from the Skill Selection Guide, bank-present values only (or omitted for coder / no-skill worker)
- [ ] **`dev-strategy` NOT embedded** in the worker message (developer-only planning skill)
- [ ] **One skill per worker** — no bundling multiple skills into one dispatch
- [ ] **Skill attached via `send_message`** — `load_skill` never rides on `spawn_instance`; use the spawn→send ritual (the target must be idle)
- [ ] **Worker message includes target, constraints, expected output** — no vague "do the thing" prompts
- [ ] **Context attached when useful** — file paths / findings / plan refs passed via `context={...}` when they'd save the child exploration time; omitted when the message already carries everything
- [ ] **Verification planned & scoped** — one targeted test/smoke (≤2-min cap) or a manual-review diff pass over the touched code (Guideline #19 fallback — no `code-review` skill in the bank); full/regression testing noted as DEFERRED → tester (Cardinal #6)
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
[How results will be verified — minimal & scoped: one targeted test/smoke (≤2-min cap) or a manual-review diff pass for the touched code (no `code-review` skill in the bank — Guideline #19 fallback). Full/regression testing DEFERRED to tester]

### Approach
[How coder/worker will run; fan-in tracking via todo_graph if 2+ instances]
```
