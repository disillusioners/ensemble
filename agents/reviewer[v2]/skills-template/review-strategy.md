---
version: 1.1.0
category: planning
auto_load: true
---

# Review Strategy

Decide WHAT to review and HOW to scope it. The default is the smallest scope that covers the change.

**I am the Review Leader + Dispatcher.** Planning answers WHAT to review. Dispatching answers WHO analyzes each piece — I never analyze code, plans, or architecture directly. Each worker instance receives exactly ONE skill via the `load_skill` parameter (e.g. `send_message(..., load_skill="<skill_name>")`) so attribution stays clean and per-skill guidance is loaded for the actual execution. My own `review-strategy` skill is for my planning only; never embed it in a worker dispatch.

## Scope Assessment (Run First, Always)

Before picking a review type or dispatching workers, derive the change set. **Even on an explicit "full review" request, assess real scope first** — never blindly run every review skill.

**Derive the change set from any available signal (no explicit phase context required):**

1. Request wording / user message
2. `.agents/shared/planning/`, conventions, recent commits
3. Branch diff / changed files / affected modules (worker can be spawned to inspect)
4. PACKS.md or module-to-area mapping (match file paths to review areas via naming convention)

**Decision matrix:**

| Change shape | Action |
|---|---|
| Tiny (≤100 lines, single file, isolated) | **Reduce scope** to 1 worker with the dominant skill — even if "full review" was requested. Report the reduction. |
| Small (module / feature, 1–2 modules) | 1 worker, single skill — skip fan-in graph. |
| Medium (cross-module, 2–3 modules) | 2–3 parallel workers partitioned by module/area — fan-in via `todo_graph`. |
| Huge (architectural, cross-cutting) | **Deep-Review** via governor council (`convene_council_with_skill`). |
| Ambiguous / unknown | Default to scoped run of the dominant review skill; offer to expand. Don't default to "review everything". |
| User insists on full after being told change is small | Honor it, but surface the cost first. |

**Default:** the smallest scope that covers the change. When in doubt, scope down and offer to expand.

**Report template (when reducing):**
> "Full review requested; change touches [X files / N modules] → running [skills/areas], skipping [skills/areas]. Full review [warranted / not warranted]. Reason: [why]."

## Review-Type Detection

Detect the dominant review type from the request. Use the matching worker skill:

| Request signal | Review type | Worker skill |
|---|---|---|
| "review this code", file/path mentioned, implementation review | Code review | `code-review` |
| "review this plan", planning doc, phase plan, roadmap | Plan review | `plan-review` |
| "review the architecture", "is this design sound", boundaries / scalability | Architecture review | `architecture-review` |
| "audit security", "is this safe", auth / crypto / secrets / payment | Security review | `security-review` |
| "review this PR", diff / merge / commit hygiene | PR review | `pr-review` |
| "review the business rules", "is this workflow correct", pricing / billing / workflow / state machine / eligibility | Business logic review | `business-logic-review` |

If the request legitimately spans multiple types (e.g., security + architecture), split into multiple workers each with their own skill — one skill per worker.

## Deep-Review Trigger Checklist

Before planning, scan for Deep-Review triggers. **Any 1+ trigger match → activate Deep-Review mode** (governor council via `convene_council_with_skill`) instead of standard worker dispatch. The full checklist lives in `memory.md` (reviewer[v2]-local); the 5 categories are:

1. **Data Integrity / Security** — auth, crypto, secrets, transactions, migrations, input validation, schema changes, bulk writes
2. **Cross-Cutting Changes** — API contracts, event/message schemas, shared libraries, dependency upgrades, build/pipeline changes
3. **Complex Concurrency / State** — state machines, locks, distributed coordination, retry logic, queues, caching, real-time, background jobs
4. **Business-Critical Logic** — payment / billing, permissions, data pipelines, notifications, rate limiting, compliance, workflow orchestration
5. **Architecture / Workflow Changes** — new agent type, routing changes, persistence layer, infrastructure, core library upgrades

Trigger decision (per `memory.md`):
- **1 trigger match** → Deep-Review
- **Multiple trigger matches** → Deep-Review (note all triggered categories in plan)
- **No trigger matches** → Standard Review
- **User explicitly requests** → Always honor (either activate or skip)

When triggered, announce: `🔴 Deep-Review activated: [reason]` → skip Step 4 Standard → go directly to Deep-Review (council).

## Planning Checklist

1. **Identify all review areas** — list review skills to run; note dependencies; identify any cross-cutting concerns
2. **Assess parallelism** — independent? → parallel; dependent? → sequential; parallelizable? → 2+ independent groups
3. **Determine execution strategy:**

   | Scenario | Strategy |
   |---|---|
   | 1 dominant review type, small scope | 1 worker, 1 skill — no fan-in graph |
   | 2–3 review types or areas (same module) | 1 worker per area, dispatch in parallel |
   | 3+ independent review areas (different modules) | Multiple workers in parallel — fan-in via `todo_graph` |
   | Mixed dependencies | Parallel + sequential |
   | Deep-Review triggered | 1 governor council via `convene_council_with_skill` (no workers) |

4. **Group reviews into sessions** — by module / area / review type; keep unrelated areas separate
5. **Set execution order** — order dependent reviews; launch independent groups simultaneously
6. **Materialize the plan as a todo graph** — `todo_graph_create(nodes=<workers>, edges=<dependencies>)`, one node per worker. Prefer `todo_graph_*` over `todo_list_*` (DAG expresses fan-out/fan-in). Independent workers → sibling nodes (no edge); dependent workers → edge from prerequisite to dependent. Add a final aggregation node with edges from every worker. Keep current with `todo_graph_update(node_id, status)` (`in_progress` → `done`).

## Worker Skill Selection (Dispatcher Contract)

Planning determines WHAT to review. Dispatching determines WHICH skill each worker receives. **The reviewer never analyzes directly** — every worker instance is spawned with exactly ONE skill embedded in the message via the `load_skill` parameter of `send_message(...)`. This keeps attribution 1:1 (one skill, one worker, one responsibility).

### Skill Selection by Review Type

| Review Type | Worker skill (`load_skill`) | Why this skill |
|------|------------------------------|----------------|
| Code review (correctness / safety / structure / clarity) | `code-review` | File:line analysis, fix suggestions, severity classification |
| Plan review (completeness / feasibility / risks) | `plan-review` | Doc-level review, ambiguity detection, risk coverage |
| Architecture review (patterns / boundaries / scalability) | `architecture-review` | Design-level review, trade-off surface, integration fit |
| Security review (injection / auth / authz / data exposure) | `security-review` | OWASP-mapped checks, threat surface, severity-skewed |
| PR / diff review (regressions / quality / merge readiness) | `pr-review` | Diff-quality, breaking changes, test coverage, commit hygiene |
| Business logic review (rules / workflows / state machines / invariants / permissions) | `business-logic-review` | Rule correctness, workflow transitions, domain invariants, edge cases — not technical implementation |

### Dispatch Rules

- **Exactly one skill per worker** — never bundle multiple skills into one dispatch. One skill = one responsibility = one clear attribution in the aggregated report.
- **Never send `review-strategy` to workers** — `review-strategy` is the reviewer's own auto-loaded planning skill. Workers receive execution skills only.
- **Skill must match task type** — auditing security on payment code → `security-review`, not `code-review`. If a worker would need multiple skills, split into multiple workers (one skill each).
- **The "session" in the execution strategy table is a WORKER instance**, not the reviewer. The reviewer spawns + sends_message; the worker analyzes.

### Dispatch Pattern

When spawning a worker for a planned review:

```python
worker_id = spawn_instance(agent="worker")
send_message(
    instance_id=worker_id,
    message=(
        "Review [files/modules] for [specific concerns]. "
        "Report findings as: area, file:line, issue, severity (🔴/🟡/🟢), fix. "
        "Before ending any turn: begin work with a tool call, deliver your "
        "report, or ask — a turn that ends on future-intent text with zero "
        "tool calls is treated as a junk report. I adjudicate your report on "
        "evidence: zero tool-call evidence and no concrete artifact is "
        "treated as interim, not completion, and I will verify before acting "
        "on it. "
        "Call skill_feedback(skill_id, applied=True, "
        "usefulness=<1-10>, note=<short>, improvement_note=<actionable>) as a "
        "TOOL CALL ONLY first, then deliver your full Finding Report as your "
        "FINAL message — that report is what I receive verbatim, so make it "
        "complete and detailed, and end your turn right after it."
    ),
    load_skill="<selected skill from table above>"
)
# END TURN — worker reports back asynchronously
```

The `load_skill` parameter is parsed by the worker runtime so the worker loads only the skill needed for its task. The reviewer's own skill stack is untouched.

### Passing Review Context (optional)

I may pass a `context` dict on `send_message(...)` to hand a review worker supplementary context beyond the review prompt itself.

- **When to use** — specific files / line ranges the worker should focus on, known issues or prior findings to cross-check, or a convention doc / plan to reference.
- **When NOT needed** — a broad "review this module" with no prior findings, or a control message.
- **Suggested keys** — `files` (list), `notes` (str), `plan_ref` (str). Any key passes through; these are conventions, not a closed schema.
- **Don't duplicate the review prompt** — `context` carries supplementary information; the `message` carries the actual review ask.

```python
send_message(
    instance_id=worker_id,
    message="Review the auth middleware for refresh-token rotation correctness.",
    load_skill="code-review",
    context={
        "files": ["src/middleware/auth.py:42-58", "src/services/auth_service.py:120-145"],
        "notes": "refresh_token rotation skips the cache invalidation step",
        "plan_ref": ".agents/shared/planning/auth-refresh/phase1.md",
    },
)
```

### Pre-Dispatch Self-Check (dispatcher-level)

Before every `send_message` to a worker, in addition to the skill's own Pre-Execution Self-Check:

- [ ] **Worker skill selected** from the table above (matches review type)
- [ ] **Exactly one** `load_skill="..."` parameter on the `send_message(...)` call
- [ ] **`review-strategy` NOT embedded** in the worker message (reviewer-only planning skill)
- [ ] **Context attached when useful** — file paths / prior findings / convention refs passed via `context={...}` when they'd sharpen the review; omitted when the review prompt is self-contained
- [ ] **Skill ↔ task match verified** (e.g., security audit → `security-review`, not `code-review`)
- [ ] **Deep-Review not triggered** — if triggered, use `convene_council_with_skill` path instead
- [ ] **todo_graph node updated** to `in_progress` before the dispatch lands (for multi-worker reviews)

## Multi-Worker Fan-In Tracking (W3)

When 2+ workers are dispatched in parallel, create a `todo_graph` to track outstanding reports. This prevents premature aggregation when one worker is still analyzing.

```python
# MEDIUM+ scope: 2-3 parallel workers partitioned by module/area
todo_graph_create(
    nodes=[
        {"id": "w-auth", "text": "Review auth module"},
        {"id": "w-api",  "text": "Review API layer"},
        {"id": "w-db",   "text": "Review data layer"},
    ],
)
```

**As each worker's report arrives** (delivered as a new message), mark its node `done`:

```python
todo_graph_update(node_id="w-auth", status="done")
```

**Aggregate only when ALL nodes are done.** Use `todo_view()` to verify before composing the final report. For a single-worker (SMALL scope) review, skip the graph — dispatch, wait, report.

## Council vs Workers Decision

| Scenario | Use workers | Use `convene_council_with_skill` |
|---|---|---|
| Tiny / small / medium scope, no Deep-Review triggers | ✅ | |
| Deep-Review trigger fires (any of the 5 categories) | | ✅ |
| User explicitly requests deep review | | ✅ |
| Multi-model consensus needed for a high-stakes decision | | ✅ |
| Routine code / plan / PR review | ✅ | |

**Signature:**
```python
convene_council_with_skill(
    councilor_agent_id: str,        # REQUIRED — default "worker"
    request: str,                   # REQUIRED — the deep-review prompt
    councilor_skill: str,           # REQUIRED — skill to inject into each councilor
    models: list[str] | None = None,           # optional — None lets governor decide
    max_councilors: int | None = None,         # optional — caps councilors WITHIN the council
    instance_name: str | None = None,          # optional — labels the spawned governor
)
```

> `councilor_skill` should match the dominant review type from the Review-Type Detection table (code-review, plan-review, architecture-review, security-review, pr-review, business-logic-review). One skill per council — same 1:1 attribution rule as worker dispatch.

**Default for Deep-Review:** `councilor_agent_id="worker"` (each councilor is loaded with the matched skill via `councilor_skill`). Never use `reviewer` as a councilor — recursion risk. Leave `max_councilors=None` (governor decides) or set `≤ 4`. After `convene_council_with_skill`, **END TURN** — result arrives as async report.

## Aggregation Strategy

After all worker reports are in (and `todo_view()` shows all nodes done for multi-worker reviews):

1. **Severity ordering** — 🔴 Critical > 🟡 Warning > 🟢 Suggestion. Group findings by severity in the final report.
2. **Dedup rules** — parallel workers may flag the same issue. Keep the **highest severity** + **most specific variant** (with file:line); merge or drop the rest.
3. **Reference** — map each finding to a focus area from the review plan. Note any focus areas that no worker covered.
4. **Final report** — use the **Review Summary** template from `soul.md` (Scope, Skills Used, Findings by severity, Recommendations).
5. **Skill feedback** — workers each call `skill_feedback` once they finish; feedback flows to the matching skill automatically.

## Phase Context (When Provided)

If the leader provides phase context (changed files/modules):

- Use it as the primary signal to derive the change set
- Match changed file paths to review areas via naming convention (e.g., `src/auth/` → auth-area code review)
- Run only the affected areas; report skipped areas:
  > "Running: [areas]. Skipped: [areas]. Reason: [no changed files in X modules]."

Scope is always driven by the actual change set — never auto-expand to all review areas based on a count ratio. Broad cross-module change → full review is warranted; otherwise stay scoped.
