# Requirements: Architect Agent (solution-architecture controller/dispatcher)

Date: 2026-08-03T14:33Z
Author: requirements-analysis worker (dispatched by planner)
Status: Draft
Source Request: "Decompose the requirements for creating a new `architect` agent in the agents-ensemble system — a solution-architecture specialist that enriches plans with architectural depth, helps the leader with hard architecture questions, and uses fan-out/fan-in to explore multiple solution approaches, then aggregates the best one. Follows the reviewer[v2] pattern (controller/dispatcher, skill-per-worker, fan-in via todo_graph, two modes: Standard + Council)."

## Stakeholders

- **Requester:** Project owner / leader agent (dispatches architect on architecture-sensitive work)
- **Affected users:** Leader (primary caller), planner (hands off plans for enrichment), developer (consumes enriched plans), reviewer (parallel review path), any agent that benefits from architectural guidance
- **Affected systems:** `agents/architect/` (new), `agents/leader/` (integration: 3 files), skill bank (new skill-set), agent registry / version-activation system

## Scope Notes (verified against repo — read before plan authoring)

> These are factual observations from inspecting the repository on 2026-08-03. They refine — and in one case correct — the dispatch's "research findings." They are not requirements; they are context the plan must account for.

1. **The v2 agents DO exist on disk.** `agents/developer[v2]/`, `agents/planner[v2]/`, `agents/reviewer[v2]/`, `agents/approver[v2]/`, and `agents/tidier[v2]/` all exist on disk. The architect agent follows the reviewer[v2] controller/dispatcher pattern as a live reference implementation. (Note: an earlier draft of this requirements doc incorrectly stated "no v2 agents exist on disk" — this was the requirements worker's original finding, which was factually wrong and is corrected here.) The architect is a greenfield agent at `agents/architect/` following the v2 structural skeleton.
2. **Directory naming: `agents/architect/` (plain path, no version tag).** Architect is a greenfield agent with no v1 predecessor. The plan-overview resolves this as `agents/architect/` (see Scope item 1 and Execution Note 1). The Q1 open question is resolved: plain path, no C1-dependency.
3. **The C1 backend bug appears ALREADY FIXED.** The dispatch flags `skill_seed_service.py` as needing a ~5-line fix so versioned-agent skills seed under the base id. Inspection of `daemon/services/skill_seed_service.py:260-265` shows it now calls `_parse_agent_dir_name(agent_dir.name)` to parse version tags. **The plan should VERIFY this fix is complete (with a regression test) rather than re-apply it**, but should not assume the bug still exists. If verification fails, the C1 fix becomes a hard dependency.
4. **`agents/reviewer/` (v1, live) has no `skill-set.yaml` or `skills-template/`.** The v1 reviewer is opencode-based. Only the *planned* v2 structure adds these. This confirms the architect must be built from the v2 planning docs, not by copying v1 files.
5. **`agents/leader/soul.md` team table is at lines 81–95** (header at 83, last row `doc-writer` at 94). The dispatch's line references (81–96) are accurate. The `wanderer` row (line 93) is a useful model for the architect row (read-only investigative specialist).
6. **`agents/leader/workflow.md` is 725 lines.** The dispatch cites invocation points at lines 114–116 (Planning Workflow), 198–220 (Domain Routing), 424–445 (Debug Phase 1.5). The plan must re-resolve exact line numbers at edit time (they drift), but the three insertion zones are confirmed structurally: Planning flow, Domain Routing, Debug Phase 1.5.
7. **Council auto-implies governor.** Per `_auth.py` `TOOL_REQUIRED_AGENTS`, declaring `"council"` in `tools.allow` automatically puts `"governor"` in the effective team_members allow-set. Explicit `team_members: ["governor"]` is redundant-but-clear (reviewer-v2 decision D3).
8. **`convene_council_with_skill` default councilor = `worker`**: `worker` has `skill_injection: true`, so skills inject correctly. The architect's Council mode MUST use `councilor_agent_id="worker"` and the required `councilor_skill` parameter. (Note: an earlier draft cited a reviewer-v2 "decision D4" claiming wanderer as the default councilor — that claim was fabricated and has been removed.)

## Functional Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-1 | The architect enriches an existing plan with architectural depth: patterns, component interactions, data flow, error-handling strategies, and cross-cutting concerns — and emits an enriched plan artifact | Leader/planner produce functional plans; architect adds the "how it fits together" layer that prevents rework during implementation | Must | Plan Enrichment |
| FR-2 | The architect answers hard architecture questions from the leader: decision analysis with ranked recommendations, trade-off matrices, and a recommended option with justification | Architecture decisions are high-cost/irreversible; leader needs a specialist's structured analysis before committing | Must | Hard Question Support |
| FR-3 | The architect explores multiple solution approaches via competitive fan-out (same-skill-different-approach): spawn N workers, each with the SAME design skill but a DIFFERENT architectural approach, then fan-in and aggregate the best (or a synthesized hybrid) | Single-approach planning hides alternatives; parallel exploration forces genuine comparison and surfaces hybrids | Must | Fan-Out/Fan-In Exploration |
| FR-4 | The architect applies design-pattern expertise via dedicated worker skills: at minimum state machine, strategy, repository, and factory patterns; observer/command/etc. grouped or individual per skill-granularity decision (Q3) | Pattern-aware architecture is more maintainable; per-pattern skills enable clean attribution and skill evolution | Should | Design Pattern Expertise |
| FR-5 | The architect presents architecture trade-offs as explicit, comparable matrices (axes: complexity, scalability, maintainability, risk, cost) with a recommended option — leaving the final decision to the leader | The leader (and user) own go/no-go on irreversible architecture; architect informs, does not decide unilaterally | Must | Trade-off Analysis |
| FR-6 | The architect operates in two modes: **Standard** (dispatch skill-equipped workers for parallel analysis) and **Council** (convene a governor council with a read-only councilor for high-stakes consensus) | Standard handles routine enrichment; Council handles contested/high-risk decisions needing multi-perspective consensus — mirrors reviewer-v2's two-mode design | Must | Operating Modes |
| FR-7 | The architect coordinates multi-worker fan-in using `todo_graph` (create tracking graph before dispatch, mark nodes done as reports arrive, escape valve when a worker is stuck) | Untracked parallel dispatch loses reports; todo_graph is the established fan-in mechanism for v2 dispatchers | Must | Fan-In Coordination |
| FR-8 | The architect's own planning skill (auto_load) performs scope assessment, mode selection (Standard vs Council), skill-per-worker dispatch planning, and blast-radius sizing — and is NEVER dispatched to a worker | Dispatchers need a private planning skill; sending it to workers defeats skill attribution and leaks coordination logic | Must | Self-Planning Skill |

### Theme: Plan Enrichment

**FR-1:** The architect accepts an existing plan (file path or inline summary) and produces an enriched version that adds: (a) architectural patterns to apply per component, (b) component-interaction descriptions (who calls whom, sync/async, failure modes), (c) data-flow descriptions (inputs, transformations, persistence, outputs), (d) error-handling/rollback strategy per component, (e) cross-cutting concerns (logging, auth, observability) and where they attach.
- **Rationale:** Planner produces the "what"; developer needs the "how it fits" to avoid architectural rework. The enrichment layer is the architect's unique value over planner+reviewer.
- **Priority:** Must
- **Notes:** Enrichment is additive — it does NOT rewrite the plan's functional scope. Output location must be decided (Q4: read-only vs. write-capable). If write-capable, enriched plan writes to a sibling file (e.g., `<plan-dir>/<plan-name>-architecture.md`) to preserve the original.

### Theme: Hard Question Support

**FR-2:** When the leader poses an architecture question (e.g., "Should we use event sourcing here?", "State machine vs. rule engine for this workflow?"), the architect produces a structured response: (a) restate the question with constraints, (b) enumerate viable options (typically 2–4), (c) per-option analysis (pros/cons/risks/cost), (d) trade-off matrix, (e) recommendation with confidence level and key assumptions, (f) what would change the recommendation.
- **Rationale:** High-stakes decisions need structure, not intuition. The leader/user make the final call; the architect ensures they decide with full information.
- **Priority:** Must
- **Notes:** For genuinely hard/contested questions, escalate to Council mode (FR-6). Standard mode suffices for questions with a clear best answer.

### Theme: Fan-Out/Fan-In Exploration

**FR-3:** For "explore approaches" requests, the architect uses the **same-skill-different-approach** model: (a) identifies N distinct viable architectural approaches (N typically 2–4), (b) spawns one worker per approach, each loaded with the SAME design skill (e.g., all get `structural-design`) but each assigned a DIFFERENT architectural approach (Approach A: state-machine, Approach B: event-driven, Approach C: strategy-pattern), (c) each worker reports feasibility, risks, effort, and a verdict for its assigned approach, (d) architect aggregates into a recommendation (best single approach OR a synthesized hybrid) along 5 fixed axes (Complexity, Scalability, Maintainability, Risk, Cost). Optionally, a meta-worker with the `trade-off-analysis` skill aggregates/compares the other workers' approaches.
- **Rationale:** Sequential single-approach thinking anchors on the first idea. Parallel exploration forces genuine comparison and surfaces hybrids.
- **Priority:** Must
- **Notes:** "Different approach per worker" is the key discipline — two workers exploring the same approach wastes a slot. The aggregation step may produce a hybrid that no single worker proposed.

### Theme: Design Pattern Expertise

**FR-4:** The architect's execution skill set covers core design patterns relevant to the codebase's architecture (state machine, strategy, repository, factory at minimum; observer, command, adapter, decorator as the skill-granularity decision allows). Each pattern skill, when loaded into a worker, guides that worker to: (a) identify whether the pattern applies, (b) sketch how it fits the component, (c) flag anti-patterns/misapplication, (d) note migration cost if refactoring toward the pattern.
- **Rationale:** Pattern fluency is the architect's expertise currency; per-pattern (or per-group) skills make that expertise deployable to workers and evolvable.
- **Priority:** Should
- **Notes:** Skill granularity is finalized (Q3 RESOLVED): the 8-skill hybrid set — 1 planning + 7 execution skills grouped by design-decision-type (not individual GoF pattern). See Constraints C-4 (maximum 7 execution skills) and the Skill Granularity Analysis below.

### Theme: Trade-off Analysis

**FR-5:** Every recommendation and every option-comparison includes an explicit trade-off matrix with consistent axes: Complexity (impl + cognitive), Scalability, Maintainability, Risk, Cost (effort + ops). The matrix is the canonical comparison artifact; prose supports it.
- **Rationale:** Consistent axes make options genuinely comparable across questions; prose-only comparisons hide unfavorable axes.
- **Priority:** Must
- **Notes:** The matrix is the architect's signature output format (analogous to reviewer's severity-calibrated findings).

### Theme: Operating Modes

**FR-6:** Standard mode: architect plans, dispatches skill-equipped workers (one skill per worker), fans in via todo_graph, aggregates, reports. Council mode: for high-stakes/contested decisions, architect convenes a governor council via `convene_council_with_skill(councilor_agent_id="worker", councilor_skill="<skill>", ...)` (worker has `skill_injection: true`), receives the synthesized consensus asynchronously, then integrates it into the final report. **Council mode triggers when any 2 of the following 4 conditions are met: (1) irreversible decision, (2) cross-system boundary change, (3) multiple viable approaches, (4) high blast radius — OR when the leader explicitly requests it.** This "any 2 of 4 + explicit request" model is more permissive than requiring all 4, avoiding the underuse risk (R6) while still gating routine work to Standard mode. Mode selection is documented in the auto-load planning skill.
- **Rationale:** Two modes match reviewer-v2's proven design and the reality that some decisions need consensus, not single-expert judgment.
- **Priority:** Must
- **Notes:** Council is non-blocking — after `convene_council_with_skill`, architect ENDS TURN; the synthesized report arrives as a new message (same pattern as worker dispatch). See FR-8 planning skill for mode-selection criteria.

### Theme: Fan-In Coordination

**FR-7:** Before any multi-worker dispatch, the architect creates a `todo_graph` with one node per worker (and per expected deliverable). As worker reports arrive, the architect marks nodes done. If a worker is stuck/missing, the fan-in escape valve activates: (1) wait grace period, (2) re-dispatch once with a clearer prompt, (3) proceed with partial results + flag DEGRADED, (4) never exceed max-re-dispatch cap. The graph is the source of truth for "are we done?"
- **Rationale:** Parallel dispatch without tracking loses reports and deadlocks aggregation. todo_graph + escape valve is the mandated v2 dispatcher pattern (prompt-writing-guide §7, checklist item 7).
- **Priority:** Must
- **Notes:** Escape valve cap is configurable; reviewer-v2 uses a stuck-worker ladder. Architect should adopt the same ladder shape.

### Theme: Self-Planning Skill

**FR-8:** The architect has exactly ONE auto_load planning skill (category: planning) that runs in the architect's own context at task start. It performs: scope assessment (read-only? enrichment? exploration? hard-question?), mode selection (Standard vs Council — with explicit criteria), skill-per-worker dispatch planning (which execution skill per worker), blast-radius sizing (how many workers, expected fan-in complexity). This skill is NEVER passed via `load_skill` to a worker.
- **Rationale:** Dispatchers need a private decision procedure; workers need focused execution skills. Mixing them breaks attribution and leaks coordination logic.
- **Priority:** Must
- **Notes:** Mirrors reviewer-v2's `review-strategy` (planning, auto_load: true) vs its 5 execution skills. Naming suggestion: `architecture-strategy`.

## Non-Functional Requirements

| ID | Category | Requirement | Metric | Target | Measurement |
|----|----------|-------------|--------|--------|-------------|
| NFR-1 | Architecture | The architect is a bounded-write dispatcher/controller — it writes enriched architecture documents to `.agents/shared/planning/` only, but never writes code or mutates source. All analysis is delegated to workers (Standard) or councilors (Council). | Direct source-code read/write calls in architect prompt prose; writes outside `.agents/shared/planning/` | 0 source mutations; 0 out-of-bound writes | Grep architect prompt files for source-mutation tool usage in prose; all analysis verbs must resolve to "dispatch a worker"; verify write boundary is `.agents/shared/planning/` only (checklist item 1) |
| NFR-2 | Consistency | The architect's file structure, meta.json schema, skill triad, and dispatch pattern match the reviewer-v2 documented pattern exactly (same skeleton, different domain content) | Structural diff vs reviewer-v2 plan's "Files to Create" table | 0 structural deviations (content differences expected) | Compare architect file list + meta.json fields against reviewer-v2 plan-overview.md §"Files to Create" and meta.json block |
| NFR-3 | Convention Compliance | The architect prompt files pass all 11 items in `docs/agent-prompt-writing-guide.md` §10 Pre-Commit Checklist | Checklist items passing | 11/11 | Run the checklist manually; the 11 items are enumerated in Constraints C-5 |
| NFR-4 | Cardinal-Rule Discipline | `rule.md` contains ≤7 Cardinal Rules; all other rules are Guidelines | Count of "Cardinal" rules in rule.md | ≤7 | Grep rule.md for cardinal-rule markers; count must be ≤7 (checklist item 4) |
| NFR-5 | Skill-Per-Worker Discipline | Exactly ONE execution skill is loaded per worker dispatch (`send_message(load_skill="<single-skill>")`); never two skills on one worker | Skills per `load_skill` argument in workflow.md dispatch examples | 1 | Inspect every dispatch example in workflow.md + planning skill; each must reference exactly one skill |
| NFR-6 | Fan-In Completeness | Every multi-worker dispatch path in workflow.md documents: (a) todo_graph creation before dispatch, (b) node-done marking on report arrival, (c) escape valve (4-step ladder + cap) | Dispatch paths with all 3 fan-in elements | 100% | Audit workflow.md dispatch sections for the 3 elements |
| NFR-7 | Prompt Hygiene | No system internals appear in prompt prose (meta.json, tools.allow, daemon/, _tool_registry, skill-set.yaml, agent_id=, seed_all, innate_skills, default_agent_versions) | Forbidden tokens in prompt prose | 0 | Grep all architect .md files for the forbidden-token list (checklist item 1) |
| NFR-8 | Skill Version Integrity | Every skill .md frontmatter `version` matches the corresponding `skill-set.yaml` entry; no drift | Mismatched versions | 0 | Cross-check each skill-set.yaml entry against its .md frontmatter (checklist item 8) |
| NFR-9 | Fallback Containment | Any fallback/dispatch-degradation instruction references only agents in the architect's `team_members`; no spawning agents outside the allow-set | Out-of-team fallback references | 0 | Grep workflow.md + skills for "spawn <agent>"; every referenced agent must be in team_members (checklist item 9) |
| NFR-10 | Maintainability | Skill set is balanced (not over-fragmented): finalized at 1 planning + 7 execution = 8 total skills (matches reviewer[v2]'s 8-skill precedent). Justification: hybrid grouping by design-decision-type (Option C from technical-analysis). | Execution skill count | 7 (justified by reviewer[v2] precedent) | Count execution entries in skill-set.yaml |
| NFR-11 | Activation | After file creation, the architect is directly usable at `agents/architect/` (plain path, no version tag); leader can spawn it | Spawn succeeds after file creation | Yes | End-to-end: leader spawns architect; architect dispatches one worker; worker reports |
| NFR-12 | Context Window | Worker reports must be ≤200 lines, structured per the Mandatory Report Format, to avoid context window overflow when the architect aggregates multiple worker reports in competitive fan-out | Worker report line count | ≤200 lines | Grep dispatch prompts for "≤200 lines" instruction; verify worker reports conform |

## Constraints

| ID | Type | Description | Source | Impact |
|----|------|-------------|--------|--------|
| C-1 | Technical | Must follow the reviewer-v2 structural pattern as documented in `.agents/shared/planning/reviewer-v2/plan-overview.md` and `.agents/shared/planning/v2-developer-planner/plan-overview.md` (file structure, meta.json schema, skill triad, dispatch pattern) | Dispatch request + established v2 convention | Architect files must match the skeleton; domain content differs |
| C-2 | Technical | meta.json must include the v2 skill triad: `skill_injection: true` + `"dynamic-skill"` in `innate_skills` + `no_force_explore: true`; plus `context_injection: {heuristic_match_shared_md_files: true}` | v2 convention (v2-developer-planner plan §"Key v2 Conventions") | Non-negotiable for skill evolution + context injection |
| C-3 | Technical | `innate_skills` must be `["todo", "chart", "dynamic-skill"]` (the v2 dispatcher triad); NO opencode anywhere | reviewer-v2 decision D6/D7; v2 convention | Removes opencode dependency entirely |
| C-4 | Technical | Skill count must be balanced. The dispatch suggests "~10–15 design-pattern skills"; this is over-fragmented per reviewer-v2 decision D5 (which rejected 10+ hyper-specific skills). Finalized at a maximum 7 execution skills (matching the finalized 8-skill hybrid set: 1 planning + 7 execution — see Skill Granularity Analysis below, NFR-10, and plan-overview.md Scope item 2). | reviewer-v2 decision D5; prompt-writing-guide maintainability; plan-overview Scope item 2 | Caps execution skills at maximum 7 (finalized set); grouping required if >7 patterns desired |
| C-5 | Convention | All 11 pre-commit checklist items must pass (`docs/agent-prompt-writing-guide.md` §10): (1) no system internals in prose, (2) one canonical home per artifact, (3) no false "stated once" claims, (4) rule.md ≤7 cardinals, (5) cross-refs resolve, (6) tone directive in soul.md, (7) fan-in escape valve in workflow.md, (8) skill versions consistent, (9) fallbacks within team_members, (10) no provenance annotations, (11) tests pass | prompt-writing-guide.md §10 | Hard gate; all 11 must pass before commit |
| C-6 | Technical | `tools.allow` must include `"instance"` (spawn_instance + send_message for dispatch), `"council"` (convene_council_with_skill for Council mode), and the standard read-only set (`bash`, `proc`, `filesystem`, `time`, `self`, `help`, `image`, `knowledge`, `mcp`, `context`, `shared_context`). It must NOT include `"db"` (mutating ops) — architect is bounded-write (NFR-1) | reviewer-v2 meta.json design + W2 read-only discipline | tools.allow is the operational contract; no mutating categories |
| C-7 | Technical | `"council"` in tools.allow auto-implies `"governor"` in effective team_members (`_auth.py` TOOL_REQUIRED_AGENTS). team_members should also list `"worker"` (dispatch target) and `"explorer"` (knowledge retrieval). Explicit `"governor"` is redundant-but-clear | reviewer-v2 decision D3 + meta.json design | team_members = `["worker", "explorer", "governor"]` (governor explicit for clarity) |
| C-8 | Technical | The leader's `team_members` MUST include `"architect"` for the leader to spawn it. This requires updating `agents/leader/meta.json` (line 16) | Dispatch request + spawn mechanics | Leader integration is a hard dependency; architect is unusable without it |
| C-9 | Technical | `skill-set.yaml` `agent_id` must use the BASE id (`"architect"`), NOT the versioned id (`"architect[v2]"`) — runtime resolution uses base id; versioned id breaks skill bank seeding | v2 convention (v2-developer-planner plan); C1 bug context | skill-set.yaml agent_id = "architect" |
| C-10 | Technical | N/A — architect ships as `agents/architect/` (no version tag), so the C1 backend fix (skill_seed_service.py parsing version tags) does NOT apply. Only versioned dirs like `agents/reviewer[v2]/` need the C1 fix. | reviewer-v2 risk C1; resolved by Q1 decision (plain path) | No action needed — C1 is irrelevant for non-versioned agents |
| C-11 | Technical | `convene_council_with_skill` is non-blocking; after invoking it, the architect must END TURN (not poll). The synthesized report arrives as a new message | reviewer-v2 decision D9; source code semantics | Workflow must document END-TURN-after-council explicitly |
| C-12 | Time | The dispatch provides comprehensive research findings; the plan phase should NOT re-research. Planning effort is MEDIUM (one agent dir + leader integration, well-documented pattern) | Dispatch request | Plan authoring scoped to structure + content, not investigation |
| C-13 | Technical | **Skill injection verification (C5):** (a) All council calls MUST use `councilor_agent_id="worker"` (worker has skill_injection:true). (b) **Pre-dispatch sanity check:** before spawning a worker with `load_skill`, the architect checks whether the skill is available in the skill bank. If not available, fall back to DEGRADED mode — spawn the worker WITHOUT a skill but with a detailed manual prompt, and flag the run as `DEGRADED — skill bank miss (<skill-name>)` in the report (reviewer[v2] skill-bank fallback pattern). (c) **Report confirmation:** the architect's dispatch prompt must instruct each worker to confirm skill loading — the worker's report must begin with either `"Skill loaded: [<skill-name>]"` or `"NO SKILL LOADED"` as its first line. | Deep-review correction set C5 | Workflow.md must document all 3 sub-items; dispatch prompts must include the confirmation instruction |
| C-14 | Technical | **Competitive aggregation criteria (W9):** When the architect aggregates multiple approaches from competitive fan-out, it must compare them along 5 fixed axes: Complexity, Scalability, Maintainability, Risk, Cost. Use a structured comparison table (see W9 template). | Deep-review correction set W9 | workflow.md aggregation step must document the 5-axis table template |


### Competitive Aggregation Table Template (W9)

When aggregating multiple approaches from competitive fan-out, the architect MUST produce this comparison table:

```
| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------------|
| A: [name] | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | [1-line] |
| B: [name] | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | [1-line] |
| C: [name] | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | [1-line] |
```

## Skill Granularity Analysis (addresses Constraint C-4 and Open Question Q3)

The dispatch floats "~10–15 design-pattern skills" (one per pattern: state machine, strategy, observer, repository, factory, command, etc.). Reviewer-v2 decision D5 explicitly **rejected** 10+ hyper-specific skills as "over-fragmentation; maintenance burden; diminishing returns." The architect should follow the same discipline.

**Finalized skill set (1 planning + 7 execution = 8 total, matching plan-overview.md):**

| Skill | Category | auto_load | Purpose |
|-------|----------|-----------|---------|
| `architecture-strategy` | planning | true | Scope assessment, mode selection (Standard/Council), competitive fan-out dispatch planning (same-skill-different-approach), blast-radius sizing — the architect's OWN decision procedure (never dispatched) |
| `structural-design` | execution | false | Structural pattern analysis: state machine, strategy, factory, command, adapter — which structural pattern fits the problem (competitive fan-out: same skill, different approaches) |
| `integration-design` | execution | false | Integration architecture design: observer/event-driven, repository, API contracts, message patterns, data transformation |
| `trade-off-analysis` | execution | false | Architecture trade-off evaluation across 5 fixed axes (Complexity, Scalability, Maintainability, Risk, Cost); also used as meta-worker in competitive fan-out |
| `scalability-analysis` | execution | false | Scalability assessment: growth projections, bottleneck identification, horizontal vs vertical scaling, capacity planning |
| `security-design` | execution | false | Security-by-design: threat modeling, attack surface mapping, auth/authz architecture, data protection patterns |
| `data-flow-modeling` | execution | false | Data flow architecture: request→response paths, event flows, state transitions, data lifecycle, normalization boundaries |
| `tech-stack-evaluation` | execution | false | Technology stack assessment: framework/library comparison, build-vs-buy, migration feasibility, team-skill alignment |

> **Change history:** The original recommendation proposed 6 skills (1 planning + 5 execution: `plan-enrichment`, `approach-exploration`, `trade-off-analysis`, `pattern-application`, `architecture-review`). This was superseded by the finalized 8-skill hybrid (Option C from technical-analysis). Specifically: `plan-enrichment` superseded by `structural-design`; `approach-exploration` superseded by the competitive fan-out model (same skill, different approaches); `pattern-application` superseded by `structural-design` + `integration-design`; `architecture-review.md` removed (collides with reviewer[v2]'s skill of the same name). See plan-overview.md Scope item 2 for the finalized set.

## Acceptance Criteria

### FR-1: Plan Enrichment

**AC-1.1** (happy path)
- **Given:** A complete plan exists at `.agents/shared/planning/<feature>/plan.md`, and the leader dispatches architect with "Enrich this plan architecturally"
- **When:** The architect processes the request in Standard mode
- **Then:** The architect returns an enriched plan artifact containing: applied patterns per component, component-interaction descriptions, data-flow descriptions, error-handling strategy, and cross-cutting concerns — each additively layered on the original scope (no functional-scope rewrite)
- **Test type:** e2e (spawn architect, provide plan, inspect output)

**AC-1.2** (edge: partial plan)
- **Given:** A plan with gaps (missing error-handling section)
- **When:** Architect enriches it
- **Then:** Architect flags the gap explicitly ("Original plan lacks error-handling; enrichment assumes X — confirm") rather than silently inventing it
- **Test type:** e2e

**AC-1.3** (error: plan file missing)
- **Given:** Leader references a plan path that doesn't exist
- **When:** Architect attempts enrichment
- **Then:** Architect reports the missing file and asks the leader to confirm the path; does NOT fabricate a plan
- **Test type:** manual

### FR-2: Hard Question Support

**AC-2.1** (happy path)
- **Given:** Leader asks "Should we use event sourcing for the audit log?"
- **When:** Architect processes the question in Standard mode
- **Then:** Architect returns: restated question + constraints, 2–4 viable options, per-option pros/cons/risks/cost, trade-off matrix (consistent axes), recommendation with confidence + assumptions, and "what would change the recommendation"
- **Test type:** e2e

**AC-2.2** (edge: clear best answer)
- **Given:** A question with one obviously-correct answer given constraints
- **When:** Architect responds
- **Then:** Architect still shows the matrix but marks the recommendation as high-confidence with a one-line rationale, rather than over-elaborating alternatives
- **Test type:** manual

### FR-3: Fan-Out/Fan-In Exploration

**AC-3.1** (happy path)
- **Given:** Leader asks "Explore approaches for the notification system"
- **When:** Architect runs Standard-mode exploration
- **Then:** Architect spawns N workers (N=2–4), each given the SAME design skill (e.g., `structural-design`) but a DIFFERENT architectural approach (same-skill-different-approach model); creates a todo_graph with N nodes; marks nodes done as reports arrive; aggregates into a recommendation along 5 fixed axes (Complexity, Scalability, Maintainability, Risk, Cost)
- **Test type:** e2e (inspect spawned instances + todo_graph state + final aggregation)

**AC-3.2** (escape valve: stuck worker)
- **Given:** One of 3 exploration workers fails to report within the grace period
- **When:** The escape valve triggers
- **Then:** Architect re-dispatches ONCE with a clearer prompt; if still stuck, proceeds with 2/3 results and flags the report DEGRADED (partial); does NOT exceed the re-dispatch cap
- **Test type:** e2e (simulate worker failure)

**AC-3.3** (discipline: distinct approaches)
- **Given:** A fan-out exploration with 3 workers
- **When:** Inspecting the 3 dispatch messages
- **Then:** Each worker's prompt assigns a DIFFERENT approach hypothesis (no two workers explore the same approach)
- **Test type:** manual / unit (assert distinct hypothesis strings)

### FR-4: Design Pattern Expertise

**AC-4.1** (happy path — state machine)
- **Given:** A component where the state-machine pattern applies
- **When:** A worker with the `structural-design` skill analyzes it
- **Then:** The worker identifies the pattern as applicable, sketches how it fits (states, transitions, guard conditions), flags misapplication risks, and notes migration cost if refactoring toward it
- **Test type:** e2e

**AC-4.2** (structural-design pattern coverage)
- **Given:** The `structural-design.md` skill template
- **When:** Inspecting its Focus Areas / pattern guidance
- **Then:** `structural-design.md` explicitly covers state machine, strategy, factory, and command patterns with application guidance for each
- **Test type:** manual (grep for "state machine", "strategy", "factory", "command" in structural-design.md)

**AC-4.3** (integration-design pattern coverage)
- **Given:** The `integration-design.md` skill template
- **When:** Inspecting its Focus Areas / pattern guidance
- **Then:** `integration-design.md` explicitly covers repository, observer/event-driven, and mediator patterns with application guidance for each
- **Test type:** manual (grep for "repository", "observer", "mediator" in integration-design.md)

### FR-5: Trade-off Analysis

**AC-5.1** (happy path)
- **Given:** Any multi-option recommendation
- **When:** Architect produces the comparison
- **Then:** Output includes a trade-off matrix with axes: Complexity, Scalability, Maintainability, Risk, Cost — each option scored/annotated on each axis; a recommended option marked with confidence
- **Test type:** e2e (parse output for matrix presence)

### FR-6: Operating Modes

**AC-6.1** (Standard mode)
- **Given:** A routine enrichment or clear-answer question
- **When:** Architect's planning skill selects a mode
- **Then:** Standard mode is selected; architect dispatches skill-equipped workers; no council convened
- **Test type:** e2e

**AC-6.2** (Council mode)
- **Given:** A high-stakes/contested architecture decision (criteria met per planning skill)
- **When:** Architect selects Council mode
- **Then:** Architect calls `convene_council_with_skill(councilor_agent_id="worker", councilor_skill="<design-skill>", request=...)`, then ENDS TURN; the synthesized consensus arrives as a new message; architect integrates it into the final report
- **Test type:** e2e

**AC-6.3** (mode-selection criteria documented)
- **Given:** The `architecture-strategy` planning skill
- **When:** Inspecting its mode-selection section
- **Then:** Explicit criteria for Standard vs Council are present: Council triggers when any 2 of 4 conditions (irreversible, cross-system boundary change, multiple viable approaches, high blast radius) are met, OR when the leader explicitly requests it; otherwise Standard
- **Test type:** manual

### FR-7: Fan-In Coordination

**AC-7.1** (todo_graph creation)
- **Given:** Any multi-worker dispatch
- **When:** Before dispatching
- **Then:** Architect creates a todo_graph with one node per worker/deliverable
- **Test type:** e2e (inspect graph state pre-dispatch)

**AC-7.2** (escape valve present)
- **Given:** The workflow.md fan-in section
- **When:** Auditing it
- **Then:** A 4-step escape valve is documented (grace period → re-dispatch once → proceed partial + DEGRADED flag → cap) — checklist item 7
- **Test type:** manual

### FR-8: Self-Planning Skill

**AC-8.1** (auto_load, never dispatched)
- **Given:** The skill-set.yaml
- **When:** Inspecting the `architecture-strategy` entry
- **Then:** `category: planning`, `auto_load: true`; and grep of workflow.md dispatch examples shows `architecture-strategy` is NEVER a `load_skill` argument
- **Test type:** manual / unit

**AC-8.2** (scope + mode + dispatch planning)
- **Given:** The `architecture-strategy` skill body
- **When:** Inspecting its sections
- **Then:** It contains scope assessment, mode selection (Standard/Council criteria), skill-per-worker dispatch planning, and blast-radius sizing
- **Test type:** manual

## Deliverables

The dispatch lists 11 deliverables. Verified against the v2 pattern (reviewer-v2 plan's "Files to Create" expects 5 agent files + skill-set.yaml + skill templates = ~12 files). The deliverable list below reconciles the dispatch's 11 with the actual v2 file structure.

| # | Deliverable | Path | Notes |
|---|-------------|------|-------|
| D-1 | Agent metadata | `agents/architect/meta.json` | v2 schema: id="architect", version="1.0.0", innate_skills=["todo","chart","dynamic-skill"], skill_injection:true, skill_search_interval:5 *(optional enhancement — not required for parity with reviewer[v2]/planner[v2]/developer[v2])*, no_force_explore:true, context_injection, tools.allow (instance+council+read-only set, NO db), team_members=["worker","explorer","governor"] |
| D-2 | Identity | `agents/architect/soul.md` | Two modes table (Standard + Council), core rule (read-only dispatcher), tone & voice with risk/severity framing, responsibilities, output-format templates (enrichment, hard-question, trade-off matrix) |
| D-3 | Rules | `agents/architect/rule.md` | ≤7 Cardinal Rules + Guidelines sections (Conduct, Parallelism, Council Invocation, Skill Feedback Contract, Skill-Bank & Knowledge, Read-Only Discipline, Never restatements) |
| D-4 | Workflow | `agents/architect/workflow.md` | Instance Naming table, Skill-Per-Worker Dispatch Pattern, Why END TURN, Fan-In Tracking (todo_graph), Fan-In Escape Valve (4-step), Skill Selection Guide, Process steps, Decision Points |
| D-5 | Tool notes | `agents/architect/tools_note.md` | Tool category sections: Instance Dispatch (PRIMARY), Council Management, Filesystem (read-only), Knowledge, Team Members, Innate Skills |
| D-6 | Skill manifest | `agents/architect/skill-set.yaml` | agent_id="architect" (BASE id), skills list (1 planning auto_load + 7 execution = 8 total, matching plan-overview.md) with name/version/auto_load/category/description |
| D-7 | Skill templates | `agents/architect/skills-template/*.md` | 8 files: architecture-strategy.md (planning, auto_load:true) + structural-design.md, integration-design.md, trade-off-analysis.md, scalability-analysis.md, security-design.md, data-flow-modeling.md, tech-stack-evaluation.md (execution, auto_load:false). (Superseded: plan-enrichment, approach-exploration, pattern-application — replaced by finalized 8-skill set; architecture-review.md removed — collides with reviewer[v2] skill of same name.) Each execution skill: frontmatter (version:"1.0.0", category:execution, auto_load:false), Pre-Execution Self-Check, Execution Contract, Focus Areas, Mandatory Report Format. |
| D-8 | Memory | `agents/architect/memory.md` | Agent memory file (pattern: minimal seed; grows via experience/knowledge) |
| D-9 | Leader meta integration | `agents/leader/meta.json` (line 16) | Add `"architect"` to `team_members` array |
| D-10 | Leader soul integration | `agents/leader/soul.md` (lines 81–95 team table) | Add architect row: `\| **architect** \| Solution-architecture specialist: enriches plans, answers hard architecture questions, explores approaches via fan-out/fan-in \| Planning workflow (after planner, before/parallel to reviewer) — when plans need architectural depth; when leader faces a hard architecture decision \|` |
| D-11 | Leader workflow integration | `agents/leader/workflow.md` (3 zones) | Add architect invocation at: (a) Planning Workflow (~lines 114–116: after planner produces plan, optional architect enrichment before reviewer), (b) Domain Routing (~lines 198–220: route architecture-sensitive requests to architect), (c) Debug Phase 1.5 (~lines 424–445: architect for structural/architectural-cause classification). Exact line numbers re-resolved at edit time. |

> **Note on Q1 (directory naming):** If the decision is `agents/architect/` (no v2 tag, new agent with no v1), then C-10 (C1 backend fix verification) does not apply — the bug only affects versioned dirs. The deliverable paths become `agents/architect/...`. The plan must resolve Q1 before authoring.

## Gaps & Ambiguities

| # | Gap / Ambiguity | Question for Caller | Severity |
|---|-----------------|---------------------|----------|
| Q1 | **Directory naming — RESOLVED.** Architect lives at `agents/architect/` (plain path, no version tag). Greenfield agent, no v1 predecessor. C1 fix does not apply. | RESOLVED: plain `agents/architect/`. See plan-overview Scope item 1. | Resolved — no longer open |
| Q2 | **team_members composition — RESOLVED.** Reviewer-v2 uses `["worker", "explorer", "governor"]`. The architect follows reviewer[v2] parity: `worker` (with filesystem+bash, read-only via skill) is sufficient for all architecture analysis. | **RESOLVED:** `team_members: ["worker", "explorer", "governor"]` — reviewer[v2] parity (decision in plan-overview.md §Scope item 2 + §Research Insight 6). | Resolved — no longer open |
| Q3 | **Skill granularity — RESOLVED.** The finalized skill set is the 8-skill hybrid (Option C from technical-analysis): 1 planning (`architecture-strategy`) + 7 execution skills. The original grouped `pattern-application` concept was superseded by dimension-specific skills (`structural-design`, `integration-design`, etc.). | RESOLVED: 8-skill hybrid per plan-overview Scope item 2. | Resolved — no longer open |
| Q4 | **Read-only vs. write-capable for enrichment — RESOLVED.** The dispatch calls architect "read-only (like reviewer)" but FR-1 (plan enrichment) implies producing/writing an enriched plan file. Is the enriched plan (a) returned inline as a report (read-only architect, leader/planner writes the file), or (b) written by the architect to a sibling file (write-capable for enrichment only)? | **RESOLVED:** The architect is a **bounded-write dispatcher** — it writes enriched architecture artifacts to `.agents/shared/planning/` only (never source code). Follows planner's "Aggregator Write Boundary" precedent (decision in plan-overview.md §Scope item 4 + §Research Insight 3; technical-analysis.md §Architecture; NFR-1). | Resolved — no longer open |
| Q5 | **Council tool confirmation — RESOLVED.** The dispatch says "Council mode with governor." Reviewer-v2 uses `convene_council_with_skill` (which auto-implies governor). Architect has `"council"` in tools.allow (enabling `convene_council_with_skill`), matching reviewer-v2. | **RESOLVED:** Council mode uses `convene_council_with_skill(councilor_agent_id="worker", councilor_skill="<skill>", ...)` exactly as reviewer-v2. `councilor_agent_id="worker"` is mandatory (worker has `skill_injection: true`) (decision in plan-overview.md §Scope item 5 + §Research Insight 6; technical-analysis.md §Current Patterns). | Resolved — no longer open |
| Q6 | **Leader workflow insertion semantics — RESOLVED.** In the Planning Workflow (D-11a), is architect enrichment (a) always run after planner, (b) optional based on plan complexity (like Approver is skipped for SMALL scope), or (c) only on explicit leader/user request? The dispatch says "enriches plans" (sounds always) but always-running adds latency. | **RESOLVED:** **Conditional** — architect enrichment is invoked only when (a) plan scope is BIG+ (matches Approver gating), (b) plan touches architectural decisions (persistence, messaging, frameworks), or (c) leader/user explicitly requests. Reduces latency for SMALL/MEDIUM scope (decision in plan-overview.md §Open Questions OQ-2 + §Scope item 4). | Resolved — no longer open |
| Q7 | **Relationship to reviewer's `architecture-review` skill — RESOLVED.** Reviewer-v2 has an `architecture-review` execution skill (reviews architecture for patterns/boundaries/scalability). The proposed architect also has an `architecture-review` skill. Are these the same skill (shared via skill bank), different skills (same name, different agent context), or should architect's be renamed (e.g., `architecture-assessment`) to avoid collision? | **RESOLVED:** Architect skill names explicitly AVOID `architecture-review` to prevent skill-bank key collision; uses dimension-specific names instead (`structural-design`, `integration-design`, etc.). Verbs differ: reviewer = **evaluate** (find flaws), architect = **generate** (propose new) (decision in plan-overview.md §Risks R3 + §Research Insight 2; phase3-plan.md §Risks P3-R1). | Resolved — no longer open |

## Assumptions

| # | Assumption | Reason | Risk if Wrong |
|---|------------|--------|---------------|
| A-1 | The reviewer-v2 pattern (file structure, schema, dispatch pattern) is the correct template for a v2 controller/dispatcher agent. The v2 agents exist on disk (`agents/developer[v2]/`, `agents/planner[v2]/`, `agents/reviewer[v2]/`, etc.) and serve as live reference implementations. | Dispatch explicitly says "follows the reviewer[v2] pattern"; v2 files confirmed on disk. | If the v2 pattern is abandoned/changed before architect ships, architect files need rework. Mitigation: v2 files are live on disk; pattern is stable. |
| A-2 | The C1 backend fix (skill_seed_service.py version-tag parsing) is already complete | Inspection shows line 265 calls `_parse_agent_dir_name`; the fix described in reviewer-v2 plan appears applied | If the fix is incomplete or regressed, versioned-agent skills won't seed. Mitigation: C-10 mandates verification + regression test. |
| A-3 | `convene_council_with_skill` semantics (non-blocking, auto-implies governor, councilor_agent_id="worker") | decisions.md cites source code (instance.py:901-956, _auth.py:35-40) with line-level evidence | If the tool changed since the decision was written, Council mode breaks. Mitigation: plan should re-verify tool signature against current source. |
| A-4 | The architect does NOT need a backend code change (unlike reviewer-v2's C1 fix) — all infrastructure (instance dispatch, council, skill bank, todo_graph) already exists | The dispatch's scope is agent prompt files + leader integration only; no backend changes mentioned | If a backend gap exists (e.g., architect needs a new tool category), scope expands. Mitigation: plan verifies tool availability during Phase 1. |
| A-5 | Worker is the correct dispatch target (not coder/developer) for architecture analysis | Worker has `skill_injection:true`, `dynamic-skill`, filesystem+bash (read-only via skill enforcement), and is the established skill-execution surface | If architecture analysis needs deeper code-execution capability than worker provides, team_members needs coder/developer (Q2). |
| A-6 | "chart" innate skill is useful for the architect (architecture diagrams) | reviewer-v2 keeps chart for architecture-review diagram generation; architect's trade-off matrices and component-interaction descriptions benefit from charts | Low risk; chart is additive, removing it only loses diagram capability. |

## Out of Scope (Deferred)

- **Backend code changes** (e.g., new tool categories, council modifications) — unless C-10 verification fails. Architect is prompt-files + leader-integration only.
- **v1 architect agent** — there is no v1; architect is greenfield. No migration content (v1→v2 notes) needed in prompt files.
- **Automated tests for architect behavior** — the dispatch asks for requirements, not tests. Test authoring is a later phase (the checklist item 11 "tests pass" refers to not breaking existing tests, not writing new architect-specific tests — though a spawn/team-member test is advisable in the plan).
- **Frontend/UI changes** — no UI surface for architect is requested. Activation is via direct leader dispatch (no version-tag settings API needed since path is plain `agents/architect/`).
- **Skill evolution tuning** — the architect's skills will evolve via skill_feedback over time; initial skill content is in scope, but ongoing tuning is out of scope.
- **Reviewer-v2 / developer-v2 / planner-v2 modification** — those v2 agents exist on disk and serve as reference implementations. Architect follows their structural pattern but does not modify them. Architect is a greenfield sibling agent.
