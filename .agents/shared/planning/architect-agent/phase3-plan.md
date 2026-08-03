# Phase 3: Skill Suite

## Objective

Create the architect's **analysis capabilities** — the 8 skill files (1 planning auto_load + 7 execution) that workers will load via `send_message(load_skill="...")` to perform architectural work. After this phase:

- `agents/architect/skill-set.yaml` declares all 8 skills with name/version/auto_load/category/description.
- `agents/architect/skills-template/` contains 8 `.md` files, each with frontmatter (version, category, auto_load), Pre-Execution Self-Check, Execution Contract, Focus Areas, and Mandatory Report Format.
- Skill names align EXACTLY with workflow.md's Skill Selection Guide (Phase 2).
- Skill versions in YAML match the frontmatter `version:` in each `.md` file.

This phase depends on Phase 1's `agent_id="architect"` only. Per W13 reordering, Phase 3 (skills) runs BEFORE Phase 2 (workflow) — workflow.md Skill Selection Guide will reference the skill names defined here.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.1 | **Create `agents/architect/skills-template/` directory.** | Phase 1 directory exists | Directory created |
| 3.2 | **Draft `agents/architect/skill-set.yaml`** with `agent_id: architect` (BASE id, NOT `"architect[v2]"`) and 8 skills listed. Skill 1: `architecture-strategy`, `version: "1.0.0"`, `auto_load: true`, `category: planning`, `description: "Architecture scope assessment, mode selection, competitive fan-out dispatch planning (same-skill-different-approach), blast-radius sizing"`. Skills 2-8: 7 execution skills (see tasks 3.9-3.15) each with `version: "1.0.0"`, `auto_load: false`, `category: execution`. Match reviewer[v2]/skill-set.yaml format exactly. | 3.1 | skill-set.yaml is valid YAML; 8 skills listed; `agent_id: architect` (no `[v2]` suffix); all execution skills are `auto_load: false` |
| 3.3 | **Draft `skills-template/architecture-strategy.md`** (the planning skill, auto_load=true). Frontmatter: `version: "1.0.0"`, `category: planning`, `auto_load: true`. Sections in order: Pre-Execution Self-Check (architect-only, never dispatched to workers — explicit warning), Scope Assessment (read-only? enrichment? exploration? hard-question?), Mode Detection (Standard vs Council criteria — 4 explicit criteria for Council, else Standard), Competitive Fan-Out (when to use it: 2+ viable approaches with no clear winner; how to assign distinct approaches to workers), Dispatch Pattern (skill-per-worker, 1 skill per worker), Blast-Radius Sizing (how many workers, expected fan-in complexity), END TURN (after dispatch). | 3.1, Phase 1 | skills-template/architecture-strategy.md exists with all 7 sections; frontmatter correct; explicit "never dispatched to workers" warning |
| 3.4 | **In `architecture-strategy.md`, include explicit Mode Detection criteria.** Council triggers when any 2 of 4 conditions are met: (a) irreversible decision, (b) cross-system impact, (c) multiple viable approaches with no clear winner, (d) high blast radius — OR when the leader explicitly requests it. Otherwise Standard. Include concrete examples: "Choosing PostgreSQL vs MongoDB for primary datastore" → Council (irreversible + cross-system = 2 of 4); "Adding a new validation rule to an existing form" → Standard (no architectural change); "Should we add a state machine to this workflow?" → Standard (single subsystem, reversible). | 3.3 | Mode Detection section has ≥4 council trigger criteria with concrete examples |
| 3.5 | **In `architecture-strategy.md`, include Competitive Fan-Out guidance (same-skill-different-approach model).** When to use: 2+ viable approaches, no clear winner, design benefits from comparison. How to assign distinct approaches: list the approaches explicitly in the dispatch message body (e.g., "Approach A: state machine. Approach B: event-driven. Approach C: hybrid."), one worker per approach. N=2-4 workers. Document the trade-off: more workers = better comparison but more fan-in complexity. | 3.3 | Competitive Fan-Out guidance present with worked example |
| 3.6 | **Verify `architecture-strategy.md` is never dispatched.** Add a verification comment to the skill's frontmatter or first section stating "ARCHITECT'S PRIVATE PLANNING SKILL — NEVER LOAD INTO A WORKER." > **NOTE:** The `workflow.md` grep check (`load_skill="architecture-strategy"` → 0 hits) is **deferred to Phase 5** because workflow.md is a Phase 2 deliverable and Phase 3 runs BEFORE Phase 2 (per W13 reordering). This is a cross-phase dependency that cannot be verified in Phase 3. Phase 5 task 5.17 takes over this verification. | 3.3 | Explicit warning in skill body; Phase 5 task 5.17 owns the workflow.md grep |
| 3.7 | **Define the standard execution-skill template** to be reused for the 7 execution skills. Template sections (in order): Frontmatter (version, category: execution, auto_load: false, description), Pre-Execution Self-Check (4-6 bullet checks), Execution Contract (Pre-Execution Self-Check → Read source if applicable → Apply skill → Report), Focus Areas (4-6 dimensions), Bounded Write Enforcement (read-only sources; do NOT mutate code; may write planning artifacts only if instructed), Mandatory Report Format (severity-calibrated findings with 🔴/🟡/🟢, or architecture-specific format). | 3.1 | Template documented; ready to instantiate for 7 skills |
| 3.8 | **Create `skills-template/structural-design.md`** — execution skill for structural pattern analysis. Focus Areas: state machine, strategy, factory, command, adapter. Worker task: given a component description, identify applicable structural pattern(s), sketch fit, flag anti-patterns, note migration cost. | 3.7 | File exists; frontmatter `version: "1.0.0"`, `category: execution`, `auto_load: false`; all template sections present |
| 3.9 | **Create `skills-template/integration-design.md`** — execution skill for integration architecture. Focus Areas: observer/event-driven, repository, API contracts, message patterns, data transformation. Worker task: given components, design how they connect (sync/async, failure modes, retry semantics). | 3.7 | File exists; frontmatter correct; all template sections present |
| 3.10 | **Create `skills-template/trade-off-analysis.md`** — execution skill for trade-off matrix construction. Focus Areas: complexity, scalability, maintainability, risk, cost (effort + ops), team-skill alignment. Worker task: given 2-4 options, build comparison matrix with consistent axes; recommend with confidence level. THIS skill is also used as the META-worker in competitive fan-out (the one that compares other workers' approaches). | 3.7 | File exists; frontmatter correct; explicitly notes dual-use (per-question dispatch OR meta-comparison worker) |
| 3.11 | **Create `skills-template/scalability-analysis.md`** — execution skill for scalability assessment. Focus Areas: growth projections, bottleneck identification, horizontal vs vertical scaling, capacity planning, scaling cliffs. Worker task: given an architecture, identify scaling limits and propose scaling strategies. | 3.7 | File exists; frontmatter correct; all template sections present |
| 3.12 | **Create `skills-template/security-design.md`** — execution skill for security-by-design. Focus Areas: threat modeling, attack surface mapping, auth/authz architecture, data protection patterns. Worker task: given an architecture, identify security risks and design mitigations INTO the architecture (not after the fact — that's reviewer's `security-review`). | 3.7 | File exists; frontmatter correct; explicit differentiation from reviewer's `security-review` |
| 3.13 | **Create `skills-template/data-flow-modeling.md`** — execution skill for data flow architecture. Focus Areas: request→response paths, event flows, state transitions, data lifecycle, normalization boundaries. Worker task: given a system, model data flow end-to-end; identify transformation points, persistence boundaries, consistency requirements. | 3.7 | File exists; frontmatter correct; all template sections present |
| 3.14 | **Create `skills-template/tech-stack-evaluation.md`** — execution skill for technology stack assessment. Focus Areas: framework/library comparison, build-vs-buy, migration feasibility, team-skill alignment, total cost of ownership. Worker task: given technology options, evaluate on dimensions above; recommend with migration plan. | 3.7 | File exists; frontmatter correct; all template sections present |
| 3.15 | **Verify all 8 skill files exist with correct frontmatter.** `ls agents/architect/skills-template/` returns 8 `.md` files. Each file's frontmatter has matching `name`, `version`, `category`, `auto_load` matching skill-set.yaml entries. | 3.2, 3.3, 3.8-3.14 | 8/8 files present; frontmatter matches YAML; no version drift |
| 3.16 | **Run convention checklist item 8 against Phase 3 files.** Cross-check skill-set.yaml version against each skill's frontmatter `version`. NFR-8 requires 0 mismatches. | 3.15 | 0 version mismatches across 8 skills |

---

## Coupling

- **Tight with:** Phase 1 (skill-set.yaml uses `agent_id="architect"` from meta.json), Phase 2 (skill names in workflow.md's Skill Selection Guide MUST match the 7 execution skill names here).
- **Loose with:** Phase 4 (leader integration does NOT reference individual skill names; only the architect as an agent).
- **Independent of:** Phase 5 (verification runs checklist against Phase 3 files but does not depend on their content).

---

## Risks (Phase 3 Specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| P3-R1 | **Skill name collision with reviewer's `architecture-review`.** Both agents have an `architecture-` prefixed skill; if the architect uses the same name, the skill bank may key incorrectly. | Medium | Low | Per Q7 resolution: architect uses specific dimension names (`structural-design`, `integration-design`, etc.), NOT `architecture-review`. Verify in task 3.15: no skill named `architecture-review` or `architecture-design`. |
| P3-R2 | **Skill version drift between YAML and frontmatter.** If YAML says `version: "1.0.0"` but a skill's frontmatter says `version: "1.0.1"`, NFR-8 fails. | Medium | Medium | Task 3.16 cross-checks; document a grep recipe for verification: `grep -E '^version:' skills-template/*.md \| awk -F: '{print $3}' \| sort -u` should produce 1 unique version value. |
| P3-R3 | **Execution skills accidentally loaded into architect context.** If `auto_load: false` is missing, the skills may auto-inject into the architect itself, polluting its context. | Medium | Low | Task 3.2 explicitly sets `auto_load: false` for all 7 execution skills. Verify in 3.15. |
| P3-R4 | **Skill bodies too generic.** If `structural-design.md` just says "design structures", it's not actionable. | Medium | High | Each skill template (3.8-3.14) requires concrete Focus Areas (4-6 dimensions) and a specific worker task statement. |
| P3-R5 | **Planning skill accidentally sent to workers.** If `architecture-strategy.md` is dispatched to a worker via `load_skill="architecture-strategy"`, the worker receives the architect's private coordination logic. | High | Low | Task 3.6 adds explicit warning in skill body (private planning skill); Phase 5 task 5.17 verifies 0 hits of `load_skill="architecture-strategy"` in workflow.md (cross-phase deferred check). |
| P3-R6 | **Skill injection keyword overlap.** If 8 skills all match "design pattern" as a trigger, injection precision drops (R1 from plan overview). | Medium | Medium | Each skill template's description (in skill-set.yaml) must use distinct keywords. `structural-design` → "pattern", "state machine", "strategy"; `integration-design` → "integration", "API", "event"; `trade-off-analysis` → "compare", "trade-off"; etc. Verify in task 3.15. |

---

## Exit Criterion

Phase 3 is DONE when ALL of the following are true:

- [ ] `agents/architect/skill-set.yaml` exists with 8 skills; `agent_id: architect` (no version tag).
- [ ] `agents/architect/skills-template/` contains exactly 8 `.md` files.
- [ ] Each skill's frontmatter version matches its skill-set.yaml entry.
- [ ] All 7 execution skills have `auto_load: false`.
- [ ] The planning skill `architecture-strategy.md` has explicit "never dispatched to workers" warning (in skill body).
- [ ] No skill is named `architecture-review` (collision avoidance).
- [ ] Convention checklist item 8 passes (NFR-8 version integrity).
- [ ] Skill names within `skills-template/` match the names listed in `skill-set.yaml` (Phase 3 internal consistency check).
- [ ] **AC-4.1 (pattern coverage — structural-design):** `structural-design.md` explicitly covers state machine, strategy, factory, and command patterns with application guidance (satisfies FR-4 minimum pattern set).
- [ ] **AC-4.2 (pattern coverage — integration-design):** `integration-design.md` explicitly covers repository, observer/event-driven, and mediator patterns with application guidance (satisfies FR-4 minimum pattern set).

> **Cross-phase note:** Workflow.md skill name verification (the `load_skill="architecture-strategy"` grep and the cross-check between workflow.md's Skill Selection Guide and skill-set.yaml) is **deferred to Phase 5** (task 5.17). workflow.md is a Phase 2 deliverable, and Phase 3 runs BEFORE Phase 2 per W13 reordering — this check cannot run in Phase 3 without a circular dependency.

**Next phase unlock:** Phase 5 verification can begin (Phase 4 Leader Integration does NOT depend on Phase 3 content).
