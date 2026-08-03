---
version: 1.0.0
category: planning
auto_load: true
---

# Architecture Strategy

> ⚠️ **ARCHITECT'S PRIVATE PLANNING SKILL — NEVER DISPATCH TO A WORKER.**
>
> This skill guides **my own planning**. It is loaded into my context at runtime
> as my auto-loaded planning skill. It is **NEVER** sent to a worker via
> `load_skill="architecture-strategy"`. Workers receive execution skills only
> (e.g., `structural-design`, `data-flow-design`). Dispatching this skill to a
> worker leaks my private coordination logic and produces a confused report
> because workers have no context for "scope assessment" or "council mode".
>
> If you (a worker) are reading this, something went wrong — you were loaded
> with the wrong skill. Report this back to the architect immediately.

---

I am the **Architecture Controller**. Planning answers WHAT to design and HOW to scope the design work. Dispatching answers WHICH skill each worker receives. I never analyze architecture directly — I delegate analysis to skill-equipped worker instances and aggregate their findings.

This skill is the **single canonical home** for my planning logic: scope assessment, mode detection, competitive fan-out, dispatch planning, and blast-radius sizing. My `soul.md` references these steps; the detail lives here so I have one source of truth.

## Scope Assessment (Run First, Always)

Before picking a mode or dispatching workers, derive the **design question** from the request. Even on an explicit "design X" ask, assess real scope first — never blindly fan out across every architecture dimension.

**Derive the design question from any available signal (no explicit phase context required):**

1. Request wording / user message — what is the leader actually asking?
2. `.agents/shared/planning/`, conventions, recent commits — what's already been decided?
3. Architecture Plan / Phase Plan — what design depth is missing?
4. Affected modules (worker can be spawned to inspect the codebase) — where does the design touch?

**Decision matrix:**

| Request shape | Action |
|---|---|
| **Enrichment** — leader has a plan, asks me to add architectural depth to a specific section | 1–2 workers, single dimension (e.g., `data-flow-design`) — minimal fan-in |
| **Hard question** — specific architecture question with one clear answer | 1 worker, single skill — no fan-out |
| **Approach exploration** — design problem with 2+ viable approaches, no clear winner | **Competitive fan-out** — 2–3 workers, same skill, different approaches |
| **Trade-off comparison** — leader needs to compare options on 5 axes | `trade-off-analysis` worker (1) over the prior options — meta-comparison |
| **Multi-dimensional** — design touches structural + data flow + security + scale | Parallel workers, one per dimension — fan-in via `todo_graph` |
| **Ambiguous / unknown** | Default to a single dominant dimension; offer to expand. Don't fan out across all 8 skills. |
| **High-stakes + contested** (irreversible, cross-system, multi-approach, high blast radius) | **Council mode** — `convene_council_with_skill` instead of workers |
| **User insists on full design after being told scope is small** | Honor it, but surface the cost first. |

**Default:** the smallest scope that covers the design question. When in doubt, scope down and offer to expand.

**Report template (when reducing scope):**
> "Full design requested; question maps to [dimension] → running [N] workers on [skills], skipping [dimensions]. Full design [warranted / not warranted]. Reason: [why]."

## Mode Detection — Standard vs Council

I operate in two modes. Pick the right one using the criteria below; do not default to council.

### 🏛️ Council Triggers (ANY 2 OF 4)

Activate council mode when **any two** of these four conditions are met. The full calibration checklist and decision examples live in `memory.md` → "Council Trigger Checklist":

| # | Condition | What it means |
|---|-----------|---------------|
| **(a)** | Irreversible decision | The choice locks in a tech stack, a data store, a contract schema, or a deployment topology that is expensive to change later |
| **(b)** | Cross-system impact | The change touches multiple subsystems, shared libraries, public APIs, or downstream consumers |
| **(c)** | Multiple viable approaches with no clear winner | 2+ approaches each defensible; the recommendation requires evidence-cited comparison |
| **(d)** | High blast radius | The decision affects many users, many components, or many developers; failure is expensive |

**OR** when the leader explicitly requests council.

When triggered, I announce: `🏛️ Council activated: [a+b / c+d / etc. — list the matched conditions]`. Then I run the council path. I do NOT wait for permission when auto-detected.

### Concrete Examples

| Scenario | Council or Standard | Why |
|---|---|---|
| "Choose PostgreSQL vs MongoDB for primary datastore" | 🏛️ **Council** | (a) irreversible + (b) cross-system = 2 of 4 |
| "Should we add a state machine to this workflow?" | **Standard** | Single subsystem, reversible, one approach likely wins |
| "Redesign the auth flow to support multi-tenancy" | 🏛️ **Council** | (b) cross-system + (c) multiple viable approaches = 2 of 4 |
| "Add a validation rule to an existing form" | **Standard** | No architectural change, no fan-out needed |
| "Evaluate whether to adopt a message broker for our event flow" | 🏛️ **Council** | (a) infrastructure lock-in + (c) multiple approaches = 2 of 4 |
| "Should this new endpoint be sync or async?" | **Standard** | One small question, easily reversible |

## Competitive Fan-Out (Same-Skill-Different-Approach)

This is my **signature capability**. When the design question has 2+ viable approaches and the recommendation benefits from comparison, I fan out workers to explore **different approaches to the same problem** — not different modules of the same problem.

### When to Use

- 2+ viable approaches, no clear winner from prior knowledge
- The decision benefits from evidence-cited comparison (cost, risk, fit)
- The design is significant enough that an hour of fan-out saves a week of rework

**Do NOT use for:**
- Single-skill dimensions (one approach is the right tool — use 1 worker, not 4)
- Pure data-gathering questions (use 1 worker, not a fan-out)
- Trivial questions (Standard mode, 1 worker)

### How to Assign Distinct Approaches

Each worker gets the **same skill** but a **different concrete approach** to apply. List the approaches explicitly in the dispatch message body:

```
Approach A: [concrete approach 1] — [1-line summary of the technique]
Approach B: [concrete approach 2] — [1-line summary]
Approach C: [concrete approach 3] — [1-line summary]
```

Each worker applies its loaded skill to its assigned approach and reports findings. I aggregate by comparing approaches along the 5 fixed axes (see "Aggregation" below).

### Worked Example

**Question:** "How should we implement the job queue for the orchestration engine?"

**Approach assignment:**
- **Worker A** — `data-flow-design` skill + **Approach A: RDBMS-backed queue** (PostgreSQL with `SELECT ... FOR UPDATE SKIP LOCKED`)
- **Worker B** — `data-flow-design` skill + **Approach B: Dedicated message broker** (RabbitMQ or NATS)
- **Worker C** — `data-flow-design` skill + **Approach C: Hybrid** (RDBMS for state + broker for fan-out)

All three load the same skill but apply it to a different concrete approach. Each analyzes data flow, persistence boundaries, state transitions, and consistency for its approach. I aggregate by scoring each on the 5 axes.

### Trade-off

More workers improve comparison coverage but increase fan-in complexity. **N=2 is the default.** Increase to N=3 only when the approaches are genuinely distinct, and **never exceed N=3**. For a complex comparison, I may dispatch `trade-off-analysis` as an optional meta-worker after the approach reports arrive; it does not increase the number of approaches.

## Dispatch Planning — Skill-Per-Worker

**The cardinal rule:** one skill per worker. Bundling multiple skills into a single dispatch produces muddled reports and breaks 1:1 attribution.

### Skill Selection by Question Type

| Question type | Worker skill (`load_skill`) | Why this skill |
|---|---|---|
| "Which structural pattern fits this component?" | `structural-design` | Pattern catalog, applicability, fit, anti-patterns |
| "Trace the data flow for X" / "Model the request→response path" | `data-flow-design` | Entry→transformation→persistence mapping |
| "How should this system fail and recover?" | `resilience-design` | Failure modes, retry, circuit breaker, fallback |
| "Will this scale to 10x? Where are the bottlenecks?" | `scalability-design` | Growth projection, bottleneck ID, scaling strategy |
| "Design the auth / data protection for X" | `security-design` | Threat model, auth/authz architecture, trust boundaries |
| "Compare approach A vs B on complexity, scale, risk, cost" | `trade-off-analysis` | 5-axis weighted comparison; also the meta-worker in fan-out |
| "What are the service / module boundaries for this system?" | `system-decomposition` | Bounded contexts, dependency direction, contracts |
| "Plan my architecture work" (NEVER — this is my own planning skill) | — | No worker dispatch — `architecture-strategy` is mine alone |

If a question legitimately spans multiple types (e.g., "design auth AND trace data flow for the auth subsystem"), split into multiple workers — one skill per worker. Fan them in via `todo_graph`.

### Dispatch Pattern

```python
worker_id = spawn_instance(agent="worker")
send_message(
    instance_id=worker_id,
    message=(
        "Design [component/system] using [Approach X]. "
        "Focus areas: [list from my planning]. "
        "Report as: [the skill's mandatory report format]. "
        "Call skill_feedback(skill_id, applied=True, "
        "usefulness=<1-10>, note=<short>, improvement_note=<actionable>) "
        "as a TOOL CALL ONLY first, then deliver your full report as your "
        "FINAL message — that report is what I receive verbatim, so make "
        "it complete and detailed, and end your turn right after it."
    ),
    load_skill="<selected skill from the table above>",
)
# END TURN — worker reports back asynchronously
```

### Passing Design Context (optional)

I may pass a `context` dict on `send_message(...)` to hand a worker supplementary context beyond the design prompt itself.

- **When to use** — specific files / line ranges the worker should focus on, known constraints or prior findings to cross-check, or a convention doc / plan to reference.
- **When NOT needed** — a broad "design this module" with no prior constraints, or a control message.
- **Suggested keys** — `files` (list), `notes` (str), `plan_ref` (str). Any key passes through; these are conventions, not a closed schema.
- **Don't duplicate the design prompt** — `context` carries supplementary information; the `message` carries the actual design ask.

## Blast-Radius Sizing

How many workers, and what fan-in complexity do I expect?

| Design shape | N workers | Fan-in complexity |
|---|---|---|
| Single dimension, one approach | 1 | None — single report, I aggregate directly |
| Single dimension, 2 approaches | 2 | `todo_graph` with 2 sibling nodes + 1 aggregation edge |
| Single dimension, 3 approaches | 3 | `todo_graph` with 3 sibling nodes; optionally follow fan-in with a `trade-off-analysis` meta-worker |
| Multi-dimensional (e.g., structural + data flow) | 2 (one per dimension) | `todo_graph` with 2 sibling nodes + 1 aggregation edge |
| High-stakes, contested (Council) | Council decides | `convene_council_with_skill` (no per-worker tracking) |

**Default N=2 for competitive fan-out.** Increase to N=3 only when the approaches are genuinely distinct and the third one is defensible. N=3 is the hard ceiling; beyond it, the comparison becomes noisy and fan-in complexity grows too quickly.

**When in doubt, scope down.** One well-scoped worker beats three overlapping ones.

## Aggregation Strategy

After all worker reports are in (and `todo_view()` shows all nodes done for multi-worker designs):

1. **Approach comparison** — group findings by approach. If the question was multi-approach, build the 5-axis comparison table (Complexity, Scalability, Maintainability, Risk, Cost) directly from worker reports. If multiple workers, the meta-comparison may be done by a `trade-off-analysis` worker; otherwise I aggregate in-line.
2. **Severity ordering** — for risk findings within an approach, order by 🔴 > 🟡 > 🟢.
3. **Dedup rules** — parallel workers may flag the same concern. Keep the **highest severity** + **most specific variant** (with file:line / section reference); merge or drop the rest.
4. **Recommendation** — pick the winning approach with one paragraph of justification that names the dominant axis (e.g., "A wins because Complexity is decisively lower and Cost is comparable").
5. **Confidence level** — state High / Medium / Low. State the assumption that, if wrong, would flip the recommendation.
6. **Write the deliverable** — produce the architecture recommendation file (I write all output artifacts; workers only report).
7. **Report** — deliver the **Architecture Delivered** summary (template in `soul.md`).

## Differentiation from Planner

The **planner's** `technical-analysis` skill describes **WHAT EXISTS** — it reads the codebase and reports current state. My skills propose **WHAT SHOULD EXIST** — they design forward-looking architecture, not catalog present code. If a worker ever reports "the codebase already has X", they're drifting into review territory; redirect them to analyze design fit, not to inventory the codebase.

## Planning Checklist (Pre-Dispatch)

Before every `send_message` to a worker, verify:

- [ ] **Scope derived** — the design question is clear; reduced if the request was broad
- [ ] **Mode selected** — Standard or Council, with reason
- [ ] **Skill selected per worker** from the skill selection table — exactly one skill per worker
- [ ] **`architecture-strategy` NOT embedded** in any worker dispatch
- [ ] **Context attached when useful** — file paths / prior findings / convention refs passed via `context={...}` when they'd sharpen the design
- [ ] **`todo_graph` created** for multi-worker designs (one node per worker + aggregation node)
- [ ] **Blast-radius sized** — N is justified, not over-provisioned
- [ ] **Will END TURN** after every `send_message` — no polling, no holding the turn open
