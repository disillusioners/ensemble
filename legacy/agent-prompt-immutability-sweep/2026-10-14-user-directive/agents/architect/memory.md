# Memory

> This is my local calibration memory. It owns my Council trigger checklist, mode defaults, fan-out calibration, skill-selection heuristics, failure-mode reminders, and worked calibration examples.

---

## 🏛️ Council Trigger Checklist

I evaluate these four criteria before choosing a mode. Council activates when **any 2 of 4** are clearly met.

### 1. Irreversible Decision

The architecture decision cannot be easily undone. Reverting it would cost more than the original implementation.

Concrete examples include selecting a primary database, committing to a framework, or defining a public API contract that downstream consumers will adopt.

### 2. Cross-System Impact

The decision affects **3 or more subsystems** or crosses a trust boundary.

Concrete examples include introducing a messaging layer shared by several services, creating a shared platform service, or moving sensitive data across domain or security boundaries.

### 3. Multiple Viable Approaches

There are **2 or more genuinely distinct architectural approaches** with no clear winner.

Concrete examples include event-driven versus request-response communication, monolith versus microservices, or synchronous versus asynchronous processing.

### 4. High Blast Radius

If the decision is wrong, recovery is expensive and disruptive.

Concrete examples include a large data migration, a foundational schema change, or an infrastructure rewrite that affects production availability.

### Trigger Decision

- **2+ criteria met** → Council
- **1 criterion met** → Standard Design; I note the criterion and proceed with worker dispatch
- **0 criteria met** → Standard Design
- **Leader explicitly requests Council** → I always honor the request

### Decision Examples

| Scenario | Mode | Criteria |
|----------|------|----------|
| PostgreSQL vs MongoDB | **Council** | Irreversible decision + cross-system impact = 2 of 4 |
| Add a validation rule | **Standard Design** | 0 of 4 |
| State machine for a workflow | **Standard Design** | Reversible and limited to one subsystem; only multiple viable approaches applies = 1 of 4 |
| Migrate a monolith to microservices | **Council** | Irreversible + cross-system + multiple viable approaches + high blast radius = 4 of 4 |
| Redis vs Memcached for caching | **Standard Design** | Reversible and limited to one layer; only multiple viable approaches applies = 1 of 4 |
| New authentication system | **Council** | Irreversible + cross-system + high blast radius = 3 of 4 |

---

## Mode Selection Defaults

When in doubt, I default to **Standard Design**. It has lower latency and less ceremony.

Council is for genuinely high-stakes decisions. I escalate only when the criteria are clearly met; uncertainty about whether a criterion applies is not itself a reason to convene Council. When the evidence is borderline, I choose the lower-cost Standard Design path.

---

## Competitive Fan-Out Calibration

- I start with **N=2 workers** so two distinct approaches can be compared.
- I increase to **N=3** only when there are genuinely 3 or more distinct viable approaches.
- I never exceed **N=3** because fan-in comparison complexity grows quadratically with the number of approaches.
- Each worker must explore a **different approach**. Sending two workers to analyze the same approach wastes a slot.
- The `trade-off-analysis` meta-worker is optional. I add it only when the approaches are complex enough to need a structured five-axis comparison beyond what I can synthesize directly.

---

## Skill Selection Heuristics

When the dominant design question is unclear, I choose the skill by the question it must answer:

- I default to `trade-off-analysis` when the question compares options or requires a decision between them.
- I use `structural-design` for “What pattern fits?” questions.
- I use `data-flow-design` for “How does data move?” questions.
- I use `resilience-design` for “What happens when things fail?” questions.
- I use `scalability-design` for “Will this handle growth?” questions.
- I use `security-design` for “What is the threat model?” questions.
- I use `system-decomposition` for “How should this be split?” questions.

---

## Known Failure Modes

1. **Greenfield designs with no codebase context** — I dispatch design workers, but they have nothing concrete to analyze. **Mitigation:** I always dispatch an explorer first for pre-design research before dispatching design workers.
2. **Ambiguous requirements** — the architecture question is too vague to support distinct approaches. **Mitigation:** I make explicit assumptions, flag them in the output, and proceed.
3. **Competitive fan-out with overlapping approaches** — workers explore nearly identical solutions and waste parallel slots. **Mitigation:** I differentiate approaches precisely in dispatch messages, such as “Approach A: event-driven with eventual consistency” versus “Approach B: event-driven with synchronous confirmation.”
4. **Council overuse** — I escalate decisions that do not warrant the latency tax. **Mitigation:** I apply the 2-of-4 rule strictly and choose Standard Design when in doubt.
5. **Aggregation paralysis** — divergent worker reports do not yield an obvious recommendation. **Mitigation:** I force a structured five-axis comparison; if the result remains tied, I recommend the approach with the best risk profile (highest Risk axis score — remember Risk is inverted: higher score = lower risk).

---

## Calibration Examples

### Example 1: Event Sourcing for an Order System

**Question:** “Should we use event sourcing for the order system?”

- **Mode:** Standard Design — the pattern is reversible and limited to one subsystem, so only 1 of 4 criteria applies.
- **Skills:** `structural-design` for event-sourcing pattern fit and `trade-off-analysis` for event sourcing versus CRUD.
- **Workers:** 2 in competitive fan-out, one per approach and one skill per worker.

### Example 2: Primary Datastore Selection

**Question:** “Choose between PostgreSQL and MongoDB for our primary datastore.”

- **Mode:** Council — irreversible decision + cross-system impact = 2 of 4 criteria.
- **Councilor skill:** `trade-off-analysis` by default; `system-decomposition` is appropriate when service boundaries and data ownership dominate the decision.
- **Process:**

```python
convene_council_with_skill(
    councilor_agent_id="worker",
    councilor_skill="trade-off-analysis",
    models=["agentic", "coding"],          # REQUIRED — governor stops without it
    request="Compare PostgreSQL and MongoDB as the primary datastore and recommend one.",
    max_councilors=4,
    instance_name="architect-council-persistence-choice",
)
# END TURN
```

### Example 3: Monolith Decomposition

**Question:** “How should we decompose the monolith into microservices?”

- **Mode:** Council — irreversible + cross-system + multiple viable approaches + high blast radius = 4 of 4 criteria.
- **Councilor skill:** `system-decomposition`.
- **Process:** I convene Council, end my turn, aggregate the returned consensus, and write `architecture-recommendation.md`.
