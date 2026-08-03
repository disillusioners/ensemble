# Phase 2: Workflow & Tools

## Objective

Create the architect's **operational mechanics** — the workflow patterns that govern how it dispatches workers, tracks fan-in, escapes stuck workers, and convenes councils; plus the tool-category guidance that tells the agent when and how to use each tool. After this phase:

- `agents/architect/workflow.md` exists with all dispatch patterns, the Skill Selection Guide, the Fan-In Escape Valve, and the Competitive Fan-Out pattern documented.
- `agents/architect/tools_note.md` exists with tool-category sections for Instance Dispatch, Council Management, Filesystem, Knowledge, Team Members, and Innate Skills.

This phase depends on Phase 1's meta.json (for `tools.allow`, `team_members`, `innate_skills`) and the skill names decided in Phase 1's soul.md "What I Design" section.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.1 | **Draft `agents/architect/workflow.md`** as a single comprehensive document with the following sections in order: Overview, Instance Naming, Skill-Per-Worker Dispatch Pattern, Why END TURN, Fan-In Tracking (todo_graph), Fan-In Escape Valve, Skill Selection Guide, Competitive Fan-Out Pattern, Process Steps (1-7), Decision Points, Council Invocation, Communication Protocol. | Phase 1 complete (meta.json schema) | workflow.md exists with all 12 sections in order |
| 2.2 | **Add Instance Naming table** to workflow.md. Conventions: `architect-worker-<approach>` (e.g., `architect-worker-event-driven`, `architect-worker-state-machine`, `architect-worker-cqrs`); `architect-council-<topic>` (e.g., `architect-council-persistence-choice`); `architect-explorer-<area>` (e.g., `architect-explorer-job-queue`). Use lowercase, hyphenated, domain-specific suffixes. | 2.1 | Instance Naming table has ≥3 example rows; naming convention documented |
| 2.3 | **Document the Skill-Per-Worker Dispatch Pattern** with a code-block example. Pattern: `worker_id = spawn_instance(agent="worker")` + `send_message(instance_id=worker_id, message=<self-contained design prompt>, load_skill="<single-skill>")`. Add explicit reminder: "ONE skill per worker dispatch — never two skills on one worker." **C5 dispatch instructions (REQUIRED):** (a) dispatch prompt must instruct: "Begin your report with either `Skill loaded: [<skill-name>]` or `NO SKILL LOADED` as its first line." (b) dispatch prompt must instruct: "Keep your report ≤200 lines, structured per the Mandatory Report Format." (c) Pre-dispatch sanity check: before spawning with load_skill, check if skill is in the skill bank; if not, spawn WITHOUT skill + flag `DEGRADED — skill bank miss (<skill>)`. Include a worked example for `structural-design` skill and a second example for `integration-design` skill. | 2.1, Phase 3 (skill names) | Dispatch pattern code-block present; ONE-skill rule explicit; C5 skill-confirmation + W7 ≤200-line instruction present; 2 worked examples |
| 2.4 | **Document Why END TURN** section. Explain: dispatching is non-blocking. After `send_message` or `convene_council_with_skill`, the architect ends its turn. Worker reports arrive as NEW MESSAGES from the system. The architect does not poll or wait. Mirrors reviewer[v2] workflow.md:46-49 and planner[v2] soul.md:111-137. | 2.1 | "Why END TURN" section explains the async pattern with cross-references |
| 2.5 | **Document Fan-In Tracking (todo_graph)** section. Before any multi-worker dispatch, architect creates `todo_graph_create(nodes=[...])` with one node per worker/deliverable. As reports arrive, architect calls `todo_graph_update(node_id=<X>, status="done")`. When ALL nodes done (verified via `todo_view()`), proceed to aggregation. Include code-block example showing graph creation for a 3-worker fan-out. | 2.1 | todo_graph pattern documented with code-block; "ALL nodes done before aggregation" rule explicit |
| 2.6 | **Document Fan-In Escape Valve** with a 4-step ladder: (1) Grace period — wait `escape_grace_period` (default 120s) for the missing worker's report. (2) Re-dispatch once with a clearer prompt if no report. (3) Proceed with partial results + flag DEGRADED in the output. (4) NEVER exceed max-re-dispatch cap (default 1). Document the escape valve as required by `docs/agent-prompt-writing-guide.md` checklist item 7. | 2.5 | 4-step ladder documented with cap; DEGRADED flag procedure explained |
| 2.7 | **Document the Skill Selection Guide** as a mapping table: question type → recommended skill. Examples: "what structural pattern fits this component?" → `structural-design`; "how do components connect?" → `integration-design`; "compare options A vs B" → `trade-off-analysis`; "will this scale to 10x?" → `scalability-analysis`; "what's the threat model?" → `security-design`; "trace data from input to persistence" → `data-flow-modeling`; "should we use framework X vs Y?" → `tech-stack-evaluation`. Include at least 10 mapping rows. Skill names MUST match the 7 execution skills in Phase 3's skill-set.yaml. | 2.1, Phase 3 (skill names locked) | Selection Guide table has ≥10 rows; skill names match Phase 3 |
| 2.8 | **Document the Competitive Fan-Out Pattern (same-skill-different-approach)** as a distinct section. Explain the difference from reviewer/planner collector fan-out: competitor workers explore DIFFERENT approaches to the SAME problem (not different modules of the same task). **Model: same-skill-different-approach** — spawn N workers (N=2-4), each given the SAME design skill (e.g., all get `structural-design`) but each assigned a DIFFERENT architectural approach (Approach A: state-machine, Approach B: event-driven, Approach C: strategy-pattern) via `context.approach` or message body. Optionally add a meta-worker with `trade-off-analysis` for aggregation/comparison. The architect aggregates by comparing along 5 fixed axes (Complexity, Scalability, Maintainability, Risk, Cost). Include a worked example for "should we use event-driven or request-response?". | 2.1 | Competitive Fan-Out section present; same-skill-different-approach model explicit; distinction from collector fan-out explicit; worked example included |
| 2.9 | **Document the 7 Process Steps** for a typical plan-enrichment invocation: (1) Receive request, (2) Mode selection (Standard vs Council), (3) Research (optional, via explorer), (4) Generate architecture plan, (5) Dispatch workers OR convene council, (6) Collect & fan-in, (7) Aggregate & deliver. Each step has a one-line description and the tool calls involved. | 2.1 | All 7 steps documented with tool references |
| 2.10 | **Document Decision Points** — explicit decision points where the architect pauses to make a judgment call. Examples: (a) after scope assessment — Standard or Council? (b) after research findings — sufficient context for dispatch, or more research needed? (c) after fan-in — proceed, re-dispatch, or DEGRADED? (d) after aggregation — confident recommendation or flag uncertainty to leader? Each decision point lists the criteria and the default action. | 2.1 | ≥4 decision points documented with criteria + defaults |
| 2.11 | **Document Council Invocation** with explicit criteria and code example. Criteria: Council triggers when any 2 of 4 conditions are met (irreversible decision, cross-system impact, multiple viable approaches, high blast radius), OR when the leader explicitly requests it. Code: `convene_council_with_skill(councilor_agent_id="worker", councilor_skill="<dominant-design-skill>", request=<high-stakes architecture prompt>, max_councilors=4)`. Then END TURN. Default councilor = `worker` (skill_injection:true, skills inject correctly). Max ONE council per question. Mirrors reviewer[v2] workflow.md:163-199. | 2.1 | Council criteria documented; code example present; default councilor + max cap explicit |
| 2.12 | **Draft `agents/architect/tools_note.md`** with tool-category sections in order: Instance Dispatch (PRIMARY — most space), Council Management (convene_council_with_skill), Filesystem (read + bounded write), Knowledge (explore + experience), Team Members (worker / explorer / governor / their capabilities), Innate Skills (todo, chart, dynamic-skill). Each section: tool name, when to use, when NOT to use, code example. | Phase 1 complete (tools.allow list) | tools_note.md exists with all 6 sections; code examples in each |
| 2.13 | **Run convention checklist items 5, 6, 7 against Phase 2 files.** Item 5: cross-refs resolve (workflow.md references to skill-set.yaml, meta.json, etc. all exist). Item 6: tone directive present in workflow.md. Item 7: Fan-In Escape Valve (4-step ladder + cap) documented in workflow.md. | 2.6, 2.11 | Checklist items 5, 6, 7 pass for Phase 2 files |

---

## Coupling

- **Tight with:** Phase 1 (workflow.md uses meta.json's `tools.allow`, `team_members`, `innate_skills`; tools_note.md enumerates the same tools), Phase 3 (workflow.md Skill Selection Guide MUST list the same 7 execution skill names as skill-set.yaml), Phase 5 (checklist verifies workflow.md items 5, 6, 7).
- **Loose with:** Phase 4 (leader workflow.md references the architect's capabilities, which are documented in workflow Phase 2; but Phase 4 does not depend on Phase 2's specific patterns).
- **Independent of:** Reviewer[v2]/planner[v2]/developer[v2] prompt files (architect is sibling, not a fork).

---

## Risks (Phase 2 Specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| P2-R1 | **Skill Selection Guide skill names drift from Phase 3.** If Phase 3 renames a skill, workflow.md's guide goes stale. | Medium | Medium | Phase 2 task 2.7 explicitly notes "skill names MUST match Phase 3's skill-set.yaml." If Phase 3 renames, Phase 2 must update in lock-step. |
| P2-R2 | **Fan-In Escape Valve too vague.** If the 4-step ladder is not explicit (with cap), the convention checklist fails. | Medium | Low | Task 2.6 specifies the ladder with default cap=1. Document explicitly. |
| P2-R3 | **Competitive Fan-Out conflated with collector fan-out.** Readers may apply reviewer/planner's collector pattern by mistake. | Medium | Medium | Task 2.8 emphasizes the distinction (DIFFERENT approaches, not different modules) and includes a worked example. |
| P2-R4 | **END TURN explanation too abstract.** Workers may not understand why they END TURN. | Medium | Low | Task 2.4 cross-references reviewer[v2] and planner[v2] patterns; explains async semantics. |
| P2-R5 | **Council criteria are subjective.** The "any 2 of 4 conditions" test may be inconsistently applied. | Medium | Medium | Task 2.11 lists the 4 criteria with concrete examples (database choice, framework selection, etc.). memory.md (Phase 5) will hold the calibration checklist. |

---

## Exit Criterion

Phase 2 is DONE when ALL of the following are true:

- [ ] `agents/architect/workflow.md` exists with all 12 sections (Overview, Instance Naming, Skill-Per-Worker Dispatch, Why END TURN, Fan-In Tracking, Fan-In Escape Valve, Skill Selection Guide, Competitive Fan-Out, Process Steps, Decision Points, Council Invocation, Communication Protocol).
- [ ] `agents/architect/tools_note.md` exists with all 6 tool-category sections.
- [ ] workflow.md contains 4-step Fan-In Escape Valve with explicit cap.
- [ ] workflow.md contains Competitive Fan-Out section with worked example.
- [ ] workflow.md Skill Selection Guide has ≥10 rows mapping question types to skills.
- [ ] workflow.md dispatch examples each reference exactly ONE skill (`load_skill="<single>"`).
- [ ] tools_note.md each section has at least one code example.
- [ ] Convention checklist items 5, 6, 7 pass for Phase 2 files.
- [ ] **W13 grep-verification:** Skill Selection Guide skill names in workflow.md match skill-set.yaml exactly (grep `name:` in skill-set.yaml, compare against Selection Guide table — zero mismatches).
- [ ] **C5 dispatch instructions:** Dispatch prompts include skill-confirmation instruction ("Skill loaded: [<skill>]" or "NO SKILL LOADED") and ≤200-line report constraint (W7).

**Next phase unlock:** Phase 5 verification covers workflow.md completeness. (Note: Phase 3 skills are created BEFORE Phase 2 per W13 reordering — workflow.md references finalized skill names.)
