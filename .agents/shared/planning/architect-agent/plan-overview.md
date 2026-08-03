# Plan Overview: Architect Agent

Date: 2026-08-03T14:43Z
Author: planner[v2] via plan-creation worker
Status: Pending Decisions
Source: Synthesized from `requirements.md` + `technical-analysis.md` + planner synthesis notes

---

## Objective

Enable the ensemble system to produce **architecture-grounded plans and recommendations** by adding a new `architect` agent that (a) enriches existing planner plans with architectural depth, (b) answers hard architecture questions from the leader with structured trade-off matrices, and (c) explores multiple solution approaches via **competitive fan-out** (N workers, each exploring a DIFFERENT approach, then aggregates the best or synthesizes a hybrid). The agent follows the v2 controller/dispatcher pattern (reviewer[v2] template) and operates in two modes: **Standard Design** (worker dispatch) and **Council** (governor convening for high-stakes decisions).

A plan is complete when the leader can spawn the architect, the architect can dispatch skill-equipped workers, the workers report back with architectural analysis, and the architect writes an `architecture-recommendation.md` to `.agents/shared/planning/<feature>/` that the developer can consume.

---

## Scope

### In Scope

| # | Item | Justification |
|---|------|---------------|
| 1 | New agent directory `agents/architect/` with full v2 prompt file set (meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml, skills-template/, memory.md) | User explicitly requested `agents/architect/` path (not `agents/architect[v2]/`). Plain path is cleaner for a greenfield agent with no v1 predecessor. |
| 2 | 8 skills: 1 planning (`architecture-strategy`, auto_load=true — competitive fan-out: same skill, different approaches) + 7 execution (`structural-design`, `integration-design`, `trade-off-analysis`, `scalability-analysis`, `security-design`, `data-flow-modeling`, `tech-stack-evaluation`) | Hybrid (Option C from technical-analysis) — matches reviewer[v2]'s 8-skill precedent. Groups skills by design-decision-type, not individual GoF pattern. Avoids skill-bank bloat from the user's original ~15-skill request. |
| 3 | Leader integration at 3 files (meta.json line 16, soul.md lines 81-95 team table, workflow.md at 3 zones) | Hard dependency — leader cannot spawn architect without these updates. |
| 4 | Write-capable architect bounded to `.agents/shared/planning/<feature>/` (mirrors planner's "Aggregator Write Boundary") | Architect WRITES enriched plan files (e.g., `architecture-recommendation.md`, `approach-comparison.md`, `architecture-decision-record.md`). Does NOT mutate source code or non-planning files. |
| 5 | Two operating modes: Standard Design (worker dispatch) + Council (governor convening) | Reviewer[v2] precedent; high-stakes architecture decisions need multi-model consensus. |
| 6 | `memory.md` with council trigger checklist (analogous to reviewer[v2]'s Deep-Review triggers) | Agent-specific configuration for mode selection. |
| 7 | Convention compliance: pass all 11 items in `docs/agent-prompt-writing-guide.md` §10 pre-commit checklist | Hard gate per C-5. |

### Out of Scope

| # | Item | Reason |
|---|------|--------|
| 1 | Backend code changes (new tool categories, council modifications, C1 backend fix) | Path is `agents/architect/` (not `[v2]`), so C1 skill-seed version-tag parsing bug does not apply. No backend changes needed. |
| 2 | Automated regression test implementation | Phase 5 RECOMMENDS a smoke test but does not implement one — testing is a separate work item. |
| 3 | Reviewer[v2] / Planner[v2] / Developer[v2] modification | Those v2 agents exist on disk and serve as reference implementations. Architect follows their pattern but does not modify them. Architect is a sibling, not a fork. |
| 4 | Skill evolution tuning | Initial content is in scope; ongoing tuning via `skill_feedback` evolves organically. |
| 5 | Frontend/UI changes | No UI surface for architect requested. Activation is via leader dispatch (no version-tag settings API needed since path is plain). |
| 6 | Per-pattern skill decomposition | Technical-analysis rejected 10+ hyper-specific pattern skills (R1 risk). Hybrid grouping is the design decision. |
| 7 | Architect's own skill versioning post-launch | Skills start at `version: "1.0.0"`; evolve via `skill_feedback` aggregation. |

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Agent Skeleton | Create meta.json, soul.md, rule.md — the agent's identity, voice, and hard rules | 13 | Foundation; phases 2/3/4 depend on it | pending |
| 2 | Workflow & Tools | Create workflow.md (dispatch patterns, fan-in, escape valve) and tools_note.md (tool category guidance) | 13 | Depends on Phase 1 (uses `instance`, `council`, `team_members` from meta.json) AND Phase 3 (workflow.md Skill Selection Guide references skill names from skill-set.yaml — skills created FIRST) | pending |
| 3 | Skill Suite | Create skill-set.yaml + 8 skills-template/*.md — the analysis capabilities | 16 | Depends on Phase 1 only (skill-set.yaml references `agent_id="architect"`). Runs BEFORE Phase 2 so workflow.md Skill Selection Guide can reference finalized skill names. | pending |
| 4 | Leader Integration | Update 3 leader files — enable leader dispatch | 10 | Independent of Phases 2/3 (only requires Phase 1's team_members schema decision) | pending |
| 5 | Verification & Docs | Create memory.md; run 11-item convention checklist; document regression test; update shared context | 18 | Depends on Phases 1-4 (verifies deliverables exist and conform) | pending |

**Total tasks across all phases:** 70 (within the 3-10-per-phase guideline where phase-level decomposition is granular; phase-level totals are coarse but each phase has its own task table).

---

## Coupling Map

|   | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---------|---------|---------|---------|---------|
| **Phase 1** | — | tight (uses meta.json `tools.allow`, `team_members`) | tight (skill-set.yaml uses `agent_id` from meta.json) | tight (leader references architect id) | tight (checklist verifies Phase 1 files) |
| **Phase 2** | tight | — | tight (workflow.md Skill Selection Guide references skill names from skill-set.yaml) | independent | tight (checklist verifies workflow.md items 5, 6, 7) |
| **Phase 3** | tight | tight (skill names must match) | — | independent | tight (checklist verifies NFR-8 skill version integrity) |
| **Phase 4** | tight | independent | independent | — | tight (checklist verifies leader integration) |
| **Phase 5** | tight | tight | tight | tight | — |

**Tight-coupling critical paths:**
- **Phase 1 ↔ Phase 3:** `skill-set.yaml.agent_id` MUST be `"architect"` (BASE id, NOT `"architect[v2]"` per C-9). The 8 skill names listed in skill-set.yaml MUST match the 8 files in `skills-template/`.
- **Phase 1 ↔ Phase 2:** `workflow.md` dispatch examples reference `instance`, `council`, and team-member names that MUST exist in meta.json.
- **Phase 2 ← Phase 3 (W13 reordered):** `workflow.md` Skill Selection Guide MUST list the same 7 execution skill names that appear in skill-set.yaml. Phase 3 (skills) runs BEFORE Phase 2 (workflow) to eliminate the race condition where workflow references skills that don't exist yet.
- **Phase 4 ↔ Phase 1:** Leader's `team_members` array MUST include `"architect"` — the exact string from meta.json's `id` field.

**Loose-coupling:**
- Phase 2 (workflow) ↔ Phase 4 (leader integration): the leader's invocation points reference the architect's role, not specific skill names. Independent of skill content.

**Independent:**
- Phase 3 (skill content) ↔ Phase 4 (leader integration): the leader does not reference individual skill names, only the architect as an agent.

---

## Component Dependency Graph

```
                           ┌─────────────────────────────┐
                           │  Phase 1: Agent Skeleton     │
                           │  meta.json  soul.md  rule.md │
                           └────────────┬─────────────────┘
                                        │
                                        ▼
                           ┌─────────────────────────────┐
                           │  Phase 3: Skills             │
                           │  skill-set.yaml              │
                           │  skills-template/×8          │
                           └────────────┬─────────────────┘
                                        │
                 ┌──────────────────────┼──────────────────────┐
                 │                                             │
                 ▼                                             ▼
    ┌───────────────────────┐                    ┌──────────────────┐
    │ Phase 2: Workflow     │                    │ Phase 4: Leader  │
    │ workflow.md           │                    │ integration      │
    │ tools_note.md         │                    │ meta.json        │
    └───────────┬───────────┘                    │ soul.md          │
                │                                │ workflow.md      │
                │                                └────────┬─────────┘
                └────────────────┬────────────────────────┘
                                 ▼
                    ┌──────────────────────────────┐
                    │ Phase 5: Verification        │
                    │ memory.md                    │
                    │ 11-item checklist run        │
                    │ regression test plan         │
                    │ shared context update        │
                    └──────────────────────────────┘
```

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1 | **Skill bank bloat (8 skills).** 8 is at the reviewer[v2] precedent ceiling. If injection precision degrades (skills compete on similar trigger keywords), workers may receive the wrong skill. | Medium | Medium | Each skill has distinct trigger keywords (structural-design → "pattern"/"state machine"/"strategy"; integration-design → "integration"/"API"/"event"; trade-off-analysis → "compare"/"trade-off"; etc.). Monitor injection precision for first 20 dispatches; if degradation, merge the two most-overlapping skills. |
| R2 | **Overlap with planner's `technical-analysis` skill.** Both agents have "trade-off" and "scalability" capabilities. Confusion over which to dispatch. | Medium | Medium | Verbal differentiation: planner `technical-analysis` is **describe what exists** (analysis); architect's skills are **propose what should exist** (design). Leader workflow.md MUST route analysis-verb to planner, design-verb to architect. Differentiation section in `architecture-strategy.md` planning skill. |
| R3 | **Overlap with reviewer's `architecture-review` skill.** Both agents touch architecture. | Low | High | Verbal differentiation: reviewer `architecture-review` is **evaluate** (find flaws in existing); architect's skills are **generate** (propose new). Architect's skill names explicitly AVOID `architecture-review` to prevent skill-bank name collision (Q7 resolution). |
| R4 | **Write-capable agent breaks "dispatcher = read-only" assumption.** | Low | Medium | Follow planner's "Aggregator Write Boundary" precedent (planner rule.md:56-58). The architect MAY write `architecture-recommendation.md` (its aggregation artifact) but workers write specialist files. Bounded to `.agents/shared/planning/<feature>/architecture-*.md` — stated operationally in rule.md. `tools.deny` does NOT include edit_file/write_file (unlike developer[v2]). |
| R5 | **Competitive fan-out is novel.** No existing agent does this. Aggregation is higher-cognitive than collector-style fan-out (reviewer/planner). | Medium | Medium | `architecture-strategy` planning skill includes a comparison framework (consistent criteria: Complexity, Scalability, Maintainability, Risk, Cost — per W9 fixed axes). `trade-off-analysis` skill can be dispatched as a META-worker to compare other workers' approaches. Monitor first 5-10 invocations; if aggregation struggles, reduce N from 3 to 2. |
| R6 | **Council overuse / underuse.** Mode-selection criteria are subjective; architect may convene council when not warranted (latency cost) or skip when warranted (single-model blind spot). | Medium | Medium | Explicit trigger criteria in `architecture-strategy.md` planning skill: Council when any 2 of 4 conditions are met (irreversible, cross-system, multiple viable approaches, high blast radius), OR when the leader explicitly requests it. Default councilor=`worker` (skill_injection:true, skills inject correctly). Max ONE council per architecture question. memory.md holds the checklist for the architect's own reference. |
| R7 | **Convention checklist gate.** One missed item (e.g., forbidden system-internals token in prose) blocks the commit. | High | Low | Phase 5 runs the 11-item checklist explicitly. Use grep recipes from `docs/agent-prompt-writing-guide.md` for items 1, 4, 5, 7, 8, 9. Manual review for items 2, 3, 6, 10, 11. |
| R8 | **Leader integration drift.** Line numbers in leader/workflow.md shift between plan and edit time. | Medium | High | Plan references sections by NAME ("Planning Workflow", "Domain Routing", "Debug Phase 1.5"), not exact lines. Editor re-resolves at edit time per the dispatch notes. |
| R9 | **Skill seeding failure for new agent.** `SkillSeedService.seed_all()` may fail or skip the new agent's 8 skills. | Medium | Low | Phase 5 verifies seeding via end-to-end smoke test (spawn architect, dispatch worker, confirm worker received skill). If seeding fails, the worker runs degraded (known per technical-analysis R1 mitigation; reviewer rule.md:61-62). |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| SC-1 | All agent files exist with correct schemas | `ls agents/architect/` returns 8 entries (meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml, skills-template/, memory.md) | 8/8 present |
| SC-2 | meta.json conforms to v2 schema | grep `innate_skills` → `["todo", "chart", "dynamic-skill"]`; grep `skill_injection: true`; grep `no_force_explore: true`; grep `context_injection.heuristic_match_shared_md_files: true`; grep `tools.allow` includes `["instance", "council", "bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_context"]` and does NOT include `"db"`; grep `team_members: ["worker", "explorer", "governor"]`; *(optional)* grep `skill_search_interval: 5` (Tier 3A caching — **optional enhancement**; no existing template agent (reviewer[v2], planner[v2], developer[v2]) sets `skill_search_interval`; including it is forward-looking but not required for parity) | All 6 schema fields correct (skill_search_interval optional) |
| SC-3 | rule.md has ≤7 Cardinal Rules | grep `^## Cardinal` or similar markers, count | ≤7 |
| SC-4 | skill-set.yaml + skills-template/ match | skill-set.yaml lists 8 skills (1 planning auto_load:true + 7 execution auto_load:false); 8 corresponding `.md` files exist in skills-template/; frontmatter versions match YAML entries | 8/8 names + versions aligned |
| SC-5 | workflow.md has all 3 fan-in elements | grep `todo_graph_create` (before dispatch); grep `todo_graph_update.*done` (marking); grep 4-step escape valve (grace period → re-dispatch → partial + DEGRADED → cap) | 3/3 elements present |
| SC-6 | workflow.md dispatch examples have exactly ONE skill per worker | grep `load_skill=` in dispatch examples, each call has one skill | 1 skill per dispatch |
| SC-7 | leader integration complete | grep `"architect"` in `agents/leader/meta.json` (line 16 area); grep `architect` row in `agents/leader/soul.md` (team table); grep `architect` in 3 zones of `agents/leader/workflow.md` | 5/5 references present |
| SC-8 | Convention checklist passes 11/11 items | Manual + grep run against items 1, 4, 5, 7, 8, 9 | 11/11 |
| SC-9 | Architect spawnable + dispatchable | End-to-end smoke test: leader dispatches architect → architect dispatches worker with `load_skill="structural-design"` → worker reports back → architect aggregates | Test passes (recommended; out-of-scope to implement) |
| SC-10 | Output file location correct | Smoke test produces `.agents/shared/planning/<feature>/architecture-recommendation.md` | File present at correct path |
| SC-11 | No forbidden system-internals tokens in prose | grep for `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `agent_id=`, `seed_all`, `innate_skills`, `default_agent_versions` across all architect .md files | 0 hits in prose |
| SC-12 | Skill injection verification (C5) | workflow.md documents: (a) councilor_agent_id="worker" mandate, (b) pre-dispatch sanity check with DEGRADED fallback for skill bank misses, (c) dispatch prompt instructs workers to begin report with "Skill loaded: [<skill>]" or "NO SKILL LOADED" | 3/3 sub-items present |
| SC-13 | Worker report size constraint (W7) | Dispatch prompts instruct "Keep your report ≤200 lines, structured per the Mandatory Report Format" | Present in all dispatch prompt templates |
| SC-14 | Competitive aggregation table (W9) | workflow.md aggregation step documents the 5-axis comparison table (Complexity, Scalability, Maintainability, Risk, Cost) | 5-axis template present |

---

## Ship Criteria

These are the go/no-go gates before the architect agent ships to the leader's team:

- [ ] **SC-G1:** Agent registry discovers `agents/architect/` with meta.json on startup (verified via smoke test — agent appears in /agents list)
- [ ] **SC-G2:** All 8 skills resolve via load_skill after seeding (grep skill-set.yaml names against skill_bank entries + runtime check: dispatch a test worker with each skill, verify "Skill loaded" confirmation)
- [ ] **SC-G3:** Leader meta.json includes "architect" in team_members (grep)
- [ ] **SC-G4:** Convention checklist (11 items from Phase 5) passes 11/11
- [ ] **SC-G5:** One manual smoke test: dispatch architect with a test question, verify it produces structured output (architecture-recommendation.md) with the correct format

---

## Research Insights

Key findings from the synthesized research that shaped this plan:

1. **Path decision: `agents/architect/` not `[v2]`.** The user explicitly requested plain path. v2 pattern uses bracket notation (e.g., `developer[v2]`), but architect has no v1 predecessor, so the plain path is cleaner and avoids the C1 backend fix verification dependency.

2. **Skill count decision: 1 + 7 = 8 total.** Technical-analysis §"Skill Suite Design" analyzed three options (A: 15 skills, B: 5 skills, C: hybrid 8 skills) and recommended Option C. The hybrid groups by design-decision-type, not individual GoF pattern. Reviewer[v2] has exactly 8 (1+7) — exact precedent match.

3. **Write-capable architect follows planner's Aggregator Write Boundary precedent.** Architect may write `architecture-recommendation.md` (its aggregation artifact) but not specialist files. Bounded to `.agents/shared/planning/<feature>/`. Does NOT need `tools.deny` for edit_file/write_file (unlike developer[v2]).

4. **Competitive fan-out is the architect's signature capability.** Differs from reviewer/planner's collector fan-out (partition by module/area) — the architect's fan-out partitions by **approach/strategy**. Documented as a distinct pattern in workflow.md.

5. **Council trigger criteria are explicit and concrete.** Council triggers when any 2 of 4 conditions are met: (a) irreversible decision, (b) cross-system boundary change, (c) multiple viable approaches with no clear winner, (d) high blast radius — OR when the leader explicitly requests it. Otherwise Standard Design dispatch.

6. **team_members = ["worker", "explorer", "governor"]** matches reviewer[v2] exactly. `governor` is technically redundant (council tool auto-implies it), but explicit is clearer.

7. **Output location is `.agents/shared/planning/<feature>/`** alongside planner output, NOT a separate `.agents/shared/architecture/` directory. Avoids split-brain.

8. **The 11-item convention checklist is the gate.** Per `docs/agent-prompt-writing-guide.md` §10, all 11 must pass before commit.

9. **Reviewer[v2]'s exact files exist on disk** (`/agents/reviewer[v2]/meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `skill-set.yaml`, `skills-template/`×7, `memory.md`) — confirmed via `ls`. Architect mirrors this structure exactly.

10. **Competitive aggregation uses 5 fixed axes (W9).** When aggregating competitive fan-out results, the architect produces a comparison table along: Complexity, Scalability, Maintainability, Risk, Cost. Template:

```
| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------------|
| A: [name] | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | [1-line] |
| B: [name] | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | [1-line] |
| C: [name] | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | Low/Med/High | [1-line] |
```

11. **Skill injection verification (C5).** All council calls use `councilor_agent_id="worker"`. Pre-dispatch sanity check: if a skill is not in the skill bank, fall back to DEGRADED mode (spawn without skill, flag `DEGRADED — skill bank miss (<skill>)`). Worker reports begin with `"Skill loaded: [<skill>]"` or `"NO SKILL LOADED"`.

12. **Worker report size constraint (W7).** Dispatch prompts instruct workers: "Keep your report ≤200 lines, structured per the Mandatory Report Format" — prevents context window overflow during competitive fan-out aggregation.

---

## Open Questions

| # | Question | Severity | Resolution Path |
|---|----------|----------|------------------|
| OQ-1 | **Council is non-blocking but how does the architect integrate the synthesized consensus?** Does it write a follow-up `architecture-decision-record.md` after receiving the council report, or include the consensus in the same `architecture-recommendation.md`? | Low | Architectural recommendation: include in same file (single source of truth per feature). Document in workflow.md. |
| OQ-2 | **Leader workflow Q6 (unresolved from requirements).** Is architect enrichment (a) always-run after planner, (b) conditional based on plan complexity (BIG+ scope only), or (c) only on explicit leader/user request? | Medium | Recommendation: conditional — only when (a) plan scope is BIG+ (matches Approver gating), (b) plan touches architectural decisions (persistence, messaging, frameworks), or (c) leader/user explicitly invokes. Reduces latency for SMALL/MEDIUM scope. Document in leader workflow.md. Developer may override. |
| OQ-3 | **Skill injection precision monitoring.** How to detect when 8 skills start competing for the same trigger keywords? | Low | Recommendation: monitor `skill_injection` logs for first 20 dispatches; if any skill is matched >50% of the time when a different skill is requested, flag for merging. Phase 5 documents this monitoring plan; implementation is post-launch. |
| OQ-4 | **Architect's response when planner's plan has gaps.** Per AC-1.2, architect flags gaps. Should the architect (a) pause and ask the leader for clarification, (b) make explicit assumptions and proceed, or (c) refuse and hand back to planner? | Low | Architectural recommendation: (b) make explicit assumptions, flag them in the output, and proceed. Leader/user can correct downstream. Matches planner's behavior on incomplete specs. |
| OQ-5 | **Architect's `memory.md` content.** Should it be minimal (just the council trigger checklist) or include calibration examples from day 1? | Low | Architectural recommendation: minimal seed (council trigger checklist + 2-3 example invocations). Grows organically via `experience()` and `skill_feedback`. |
| OQ-A | **Governor team_members.** Does the governor need architect in ITS team_members for the council to work? | Medium | **RESOLVED — NOT needed.** Architect spawns governor; governor spawns councilors from `councilor_agent_id` specified by architect (which will be `worker`). No change to governor needed. |
| OQ-B | **Worker contention.** Multiple architect instances spawning workers simultaneously could exhaust the worker pool. | Medium | **RESOLVED — Acceptable risk.** The system `system_parallel_queue` concurrency limit is **5** (not 3). The dispatch convention for parallel workers is up to 3 (a self-imposed limit, not system-enforced), which is more conservative than the system's limit of 5. Documented as a known limitation. |
| OQ-C | **Skill seeding.** How to verify all 8 skills seed correctly via `load_skill` after agent creation? | Medium | **RESOLVED — Phase 5 smoke test.** Add a Phase 5 smoke test that verifies all 8 skills resolve via `load_skill` after seeding. (Task 5.13 smoke test enhanced to cover this.) |
| OQ-D | **Single-actor skill_feedback.** Only the architect can provide skill_feedback, but workers are the ones who actually use the skills. | Low | **RESOLVED — Acceptable for now.** Workers can provide `skill_feedback` too since they have the `dynamic-skill` innate skill. The architect passes through worker feedback. |

---

## Assumptions

| # | Assumption | Reason | Risk if Wrong |
|---|------------|--------|---------------|
| A-1 | The reviewer[v2] pattern (file structure, schema, dispatch pattern) is the correct template for a v2 controller/dispatcher agent. | Pattern is well-documented in `.agents/shared/planning/reviewer-v2/plan-overview.md` and `.agents/shared/planning/v2-developer-planner/plan-overview.md`. Reviewer[v2] files exist on disk (confirmed). | If the pattern is abandoned/changed before architect ships, architect files need rework. Mitigation: reviewer[v2] is live on disk; pattern is stable. |
| A-2 | `convene_council_with_skill` semantics (non-blocking, auto-implies governor, `councilor_agent_id="worker"`). | Decisions cite source code (`instance.py:901-956`, `_auth.py:35-40`) with line-level evidence. | If tool signature changed, Council mode breaks. Mitigation: Phase 1 verifies `convene_council_with_skill` is available in current tools registry. |
| A-3 | The architect does NOT need backend code changes — all infrastructure (instance dispatch, council, skill bank, todo_graph, `load_skill`) already exists. | Path is `agents/architect/` (not `[v2]`), so C1 fix is irrelevant. No new tool categories needed. | If a backend gap exists (e.g., a new tool is required), scope expands. Mitigation: Phase 1 verifies all required tools are available. |
| A-4 | `worker` is the correct dispatch target (not `coder`/`developer`) for architecture analysis. | Worker has `skill_injection:true`, `dynamic-skill`, filesystem+bash, and is the established skill-execution surface. | If architecture analysis needs deeper code-execution capability, team_members needs to include `coder` or `developer`. Mitigation: Phase 1 documents the trade-off; can be added later if needed. |
| A-5 | The `chart` innate skill is useful for the architect (architecture diagrams). | Reviewer[v2] keeps chart for diagram generation; architect's trade-off matrices and component-interaction descriptions benefit from charts. | Low risk; chart is additive, removing it only loses diagram capability. |

---

## File Inventory

### Files to create in `agents/architect/`

| File | Purpose | Phase | Size estimate |
|------|---------|-------|---------------|
| `meta.json` | Agent metadata (v2 schema) | 1 | ~50 lines |
| `soul.md` | Identity, voice, modes, responsibilities, output formats | 1 | ~120 lines |
| `rule.md` | Cardinal rules (≤7) + guidelines | 1 | ~150 lines |
| `workflow.md` | Dispatch patterns, fan-in, escape valve, skill selection | 2 | ~250 lines |
| `tools_note.md` | Tool category guidance | 2 | ~100 lines |
| `skill-set.yaml` | Skill manifest (8 skills) | 3 | ~40 lines |
| `skills-template/architecture-strategy.md` | Planning skill (auto_load) | 3 | ~120 lines |
| `skills-template/structural-design.md` | Execution skill | 3 | ~100 lines |
| `skills-template/integration-design.md` | Execution skill | 3 | ~100 lines |
| `skills-template/trade-off-analysis.md` | Execution skill | 3 | ~100 lines |
| `skills-template/scalability-analysis.md` | Execution skill | 3 | ~100 lines |
| `skills-template/security-design.md` | Execution skill | 3 | ~100 lines |
| `skills-template/data-flow-modeling.md` | Execution skill | 3 | ~100 lines |
| `skills-template/tech-stack-evaluation.md` | Execution skill | 3 | ~100 lines |
| `memory.md` | Council trigger checklist + calibration | 5 | ~80 lines |

**Total: 15 files** (7 root files in `agents/architect/`: meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml, memory.md + 8 skill templates in `skills-template/`: architecture-strategy.md + 7 execution skills = 7 + 8 = 15 architect files). The 3 leader file updates (meta.json, soul.md, workflow.md) are counted separately.

### Files to modify in `agents/leader/`

| File | Change | Phase | Size estimate |
|------|--------|-------|---------------|
| `meta.json` | Add `"architect"` to `team_members` array (line 16 area) | 4 | 1 line |
| `soul.md` | Add architect row to team table (lines 81-95 area) | 4 | 1 row |
| `workflow.md` | Add 3 invocation points (Planning, Domain Routing, Debug Phase 1.5) | 4 | ~30 lines across 3 zones |

**Total: 3 leader files updated.**

### Files to create in `agents/shared/` (optional)

| File | Purpose | Phase | Size estimate |
|------|---------|-------|---------------|
| (none required) | — | — | — |

### Files NOT to modify

- `agents/reviewer[v2]/` — unchanged (architect is sibling, not fork).
- `agents/planner[v2]/` — unchanged.
- Backend code (`daemon/`, `services/`) — no changes.

---

## Execution Notes

1. **Order of execution (W13 corrected):** Phase 1 → Phase 3 → (Phase 2 + Phase 4 in parallel) → Phase 5.
   - **Phase 3 (skills) runs BEFORE Phase 2 (workflow)** because workflow.md's Skill Selection Guide references skill names that must exist in skill-set.yaml first. This resolves the Phase 2↔3 race condition.
   - Phase 4 (leader integration) can run in parallel with Phase 2 since it references only the architect as an entity, not its internal skill content.
   - Phase 5 must run last (verifies all of Phases 1-4).

2. **Phase 4 independence:** Leader integration does NOT depend on skill content (Phase 3) or workflow patterns (Phase 2). It only requires Phase 1's `agent_id="architect"` decision. This means a developer can land Phase 4 in a separate PR if desired.

3. **Skill name stability:** The 8 skill names listed in the synthesis notes are LOCKED. Any rename (e.g., `architecture-review` → `architecture-design`) requires a coordinated update across skill-set.yaml, all 8 skills-template/*.md files, and workflow.md's Skill Selection Guide.

4. **Output file naming:** The 3 architect output files use specific names that must match between skill reports and any code that reads them: `architecture-recommendation.md`, `approach-comparison.md`, `architecture-decision-record.md`.
5. **W13 grep-verification (Phase 2 acceptance criterion):** After writing workflow.md, grep the Skill Selection Guide skill names against skill-set.yaml to confirm they match exactly. Command: extract skill names from workflow.md Selection Guide table, compare against `grep "name:" agents/architect/skill-set.yaml`. Zero mismatches required. This catches the Phase 2↔3 race condition where workflow references skills that don't exist or are misnamed.

---

## Verbatim Leader/Workflow.md Insertion Blocks (W16)

These are copy-paste-ready blocks for the developer to insert into `agents/leader/workflow.md`. Each block includes the architect invocation trigger criteria and the dispatch/receive contract.

### Block 1: Planning Workflow Zone (~line 116)

```markdown
### Architect Enrichment (Conditional — BIG+ scope or architectural decisions)
**Trigger:** Plan involves system boundaries, new core patterns, infrastructure migration, OR scope is BIG+.
**Dispatch:** `spawn_instance(agent="architect")` → `send_message(instance_id, message="Enrich this plan with architectural depth: [plan ref]. Focus areas: [list]. Output to .agents/shared/planning/<feature>/architecture-recommendation.md")`
**Receive:** Architect returns enriched architecture-recommendation.md path + summary. Leader incorporates into plan before Reviewer.
```

### Block 2: Domain Routing Zone (~lines 198-220)

```markdown
### Architecture Decision Routing
**Trigger phrases:** "should we use X or Y?", "new persistence layer", "new pattern", "framework selection", "cross-system change", "architecture decision", "structural change", "how should we design"
**Route to:** `spawn_instance(agent="architect")` → `send_message(instance_id, message="Architecture decision needed: [question]. Constraints: [list]. Provide trade-off matrix with recommendation.")`
**Receive:** Architect returns `architecture-recommendation.md` with trade-off matrix (5-axis: Complexity, Scalability, Maintainability, Risk, Cost) + recommended option. Leader reviews before dispatching to developer.
**Do NOT route to architect for:** functional scope questions (→ planner), code quality review (→ reviewer), implementation details (→ developer).
```

### Block 3: Debug Phase 1.5 Zone (~lines 424-445)

```markdown
### Architect as Debug Investigator (BIG+ multi-system bugs only)
**Trigger:** Bug involves cross-system architectural boundaries, suspicious coupling, unclear component ownership, OR failure path spans 3+ subsystems.
**Dispatch:** `spawn_instance(agent="architect")` → `send_message(instance_id, message="Map the architecture around this failure path: [bug description]. Focus: component boundaries, coupling, data flow along the failure path. Output to .agents/shared/planning/<feature>/architecture-recommendation.md")`
**Receive:** Architect returns architecture map of the failure area + identified coupling/boundary issues. Leader uses this alongside planner's failure-path mapping to guide the developer's fix.
**Do NOT invoke for:** single-component bugs, clear-cause bugs, syntax errors, configuration issues.
```
.
```
