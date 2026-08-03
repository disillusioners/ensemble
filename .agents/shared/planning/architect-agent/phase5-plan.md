# Phase 5: Verification & Documentation

## Objective

Verify all Phase 1-4 deliverables against the convention checklist, create the architect's `memory.md` for self-calibration, document a smoke-test plan, and update shared project context. After this phase:

- `agents/architect/memory.md` exists with the council trigger checklist and calibration examples.
- All 11 items in `docs/agent-prompt-writing-guide.md` §10 pre-commit checklist pass.
- The end-to-end smoke test is documented (recommendation; implementation is out-of-scope).
- Shared project context (`agents/shared/context.md`) is updated to note the architect's existence.
- A skill-feedback monitoring plan is documented for the first 20 dispatches.

This phase depends on Phases 1-4 (verifies deliverables exist and conform).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 5.1 | **Draft `agents/architect/memory.md`** — the architect's self-calibration document. Sections: (a) Council Trigger Checklist — the 4 criteria (irreversible, cross-system, multiple viable approaches, high blast radius) with concrete examples per criterion; (b) Mode Selection Defaults — when in doubt, default to Standard (lower latency, less ceremony); (c) Competitive Fan-Out Calibration — start with N=2 workers, increase to N=3 only if approaches are genuinely distinct; (d) Skill Selection Heuristics — when unsure which skill to load, default to `trade-off-analysis` (covers the broadest dimension); (e) Known Failure Modes — list 3-5 patterns the architect has historically struggled with (e.g., greenfield designs with no codebase context, ambiguous requirements, etc.); (f) Calibration Examples — 2-3 example invocations with the chosen mode and skill. | Phases 1, 3 complete | memory.md exists with all 6 sections; council trigger checklist has 4 criteria + examples |
| 5.2 | **Run convention checklist item 1: no system internals in prose.** Grep all architect `.md` files (soul.md, rule.md, workflow.md, tools_note.md, skills-template/*.md, memory.md) for forbidden tokens: `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `agent_id=`, `seed_all`, `innate_skills`, `default_agent_versions`. Zero hits in prose (within ` ``` ` code blocks is acceptable but should be flagged). | Phases 1-3 complete | 0 hits across all architect .md files (excluding YAML/JSON metadata and fenced code blocks) |
| 5.3 | **Run convention checklist item 2: one canonical home per artifact.** Verify the 8 agent files (meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml, skills-template/, memory.md) are each present in exactly ONE location — `agents/architect/`. Verify there is no duplicate content scattered across `agents/shared/` or elsewhere. | Phases 1-3 complete | All 8 files present in `agents/architect/` only; no duplicates |
| 5.4 | **Run convention checklist item 3: no false "stated once" claims.** Search for phrases like "as stated earlier", "previously mentioned", "as documented above" without an actual prior reference. Verify all cross-references between files resolve to real content. | Phases 1-3 complete | No false "stated once" claims; all cross-refs resolve |
| 5.5 | **Run convention checklist item 4: rule.md ≤7 cardinals.** Count Cardinal Rules in `rule.md`. Verify count is exactly 6 (target) and ≤7 (constraint). | Phase 1 complete | Cardinal rule count ≤7; target = 6 |
| 5.6 | **Run convention checklist item 5: cross-refs resolve.** Verify all references in soul.md, rule.md, workflow.md, tools_note.md, memory.md to other files (e.g., "see skill-set.yaml", "per reviewer[v2] pattern") point to real content. | Phases 1-3 complete | All cross-refs resolve to real files/sections |
| 5.7 | **Run convention checklist item 6: tone directive in soul.md.** Verify soul.md has a "Tone & Voice" section with explicit guidance (e.g., severity framing, formal/structured register, no hedging language). | Phase 1 complete | Tone & Voice section present with explicit directives |
| 5.8 | **Run convention checklist item 7: Fan-In Escape Valve in workflow.md.** Verify workflow.md has the 4-step ladder (grace period → re-dispatch → partial + DEGRADED → cap) with explicit cap value (default 1). | Phase 2 complete | 4-step ladder documented with cap |
| 5.9 | **Run convention checklist item 8: skill version consistency.** Cross-check `skill-set.yaml` version against each skill's frontmatter `version:` in `skills-template/*.md`. Zero mismatches. | Phase 3 complete | 0 version mismatches across 8 skills |
| 5.10 | **Run convention checklist item 9: fallbacks within team_members.** Grep workflow.md, tools_note.md, soul.md for references to agents NOT in `team_members: ["worker", "explorer", "governor"]`. Zero out-of-team references. | Phases 1-3 complete | 0 out-of-team fallback references |
| 5.11 | **Run convention checklist item 10: no provenance annotations.** Search for phrases like "TODO", "FIXME", "XXX", "HACK", "PLACEHOLDER", "DRAFT" in agent prompt files. Zero hits (or hits are explicitly marked as intentional). | Phases 1-3 complete | 0 provenance annotations (or explicitly intentional) |
| 5.12 | **Run convention checklist item 11: tests pass.** Run existing test suite (`pytest` or equivalent). No new failures introduced by the architect's files (architect is additive; no existing code modified). Document test run output. | Phases 1-4 complete | Existing tests pass; architect is additive (no existing tests broken) |
| 5.12b | **Verify skill injection verification requirements (C5).** Check workflow.md dispatch patterns: (a) all council calls use `councilor_agent_id="worker"`; (b) pre-dispatch sanity check with DEGRADED fallback is documented; (c) dispatch prompts instruct workers to begin report with "Skill loaded: [<skill>]" or "NO SKILL LOADED"; (d) dispatch prompts include "Keep your report ≤200 lines" (W7). | Phase 2 complete | 4/4 C5/W7 sub-items present in workflow.md dispatch patterns |
| 5.17 | **Cross-check workflow.md Skill Selection Guide vs skill-set.yaml (deferred from Phase 3 task 3.6).** Workflow.md is a Phase 2 deliverable, so this check cannot run in Phase 3 (would create a circular dependency since Phase 3 runs BEFORE Phase 2 per W13). Phase 5 verifies: (a) `grep load_skill="architecture-strategy" agents/architect/workflow.md` returns 0 hits (architecture-strategy is the architect's private planning skill, never dispatched to workers); (b) every skill name referenced in workflow.md's Skill Selection Guide matches a name declared in `skill-set.yaml` (no typos, no orphan references, no missing skills); (c) every name in `skill-set.yaml` is either referenced in workflow.md OR explicitly noted as "architect-internal" (e.g., `architecture-strategy`). | Phase 2 + Phase 3 complete | 0 hits of `load_skill="architecture-strategy"` in workflow.md; all skill-name references between workflow.md and skill-set.yaml are consistent |
| 5.13 | **Document an end-to-end smoke test plan** in `.agents/shared/planning/architect-agent/smoke-test.md`. Test: leader spawns architect → architect receives "Enrich plan for feature X" request → architect spawns 1-2 workers with skill loads → workers report back → architect aggregates → writes `architecture-recommendation.md` → leader receives final report. **OQ-C (REQUIRED): Include a skill-seeding verification step** — verify all 8 skills resolve via `load_skill` after seeding (spawn a worker with each of the 8 skills, confirm "Skill loaded: [<skill>]" appears in each report). Steps, expected output, pass/fail criteria. Implementation of the test itself is OUT OF SCOPE (recommendation only). | Phases 1-4 complete | smoke-test.md exists with test plan including 8-skill seeding verification; marked as recommendation, not implementation |
| 5.14 | **Update `.agents/shared/context.md`** to note the new architect agent's existence and primary responsibilities. Add a brief entry under the "Active Agents" section. | Phases 1-4 complete | context.md has new entry for architect |
| 5.15 | **Document the skill-feedback monitoring plan** in `.agents/shared/planning/architect-agent/post-launch-monitoring.md`. For the first 20 architect dispatches: monitor `skill_injection` logs for injection precision, monitor `skill_feedback` for usefulness scores. If any skill consistently scores <5/10 usefulness, flag for merging or revision. Plan includes: monitoring metrics (injection precision, usefulness avg, dispatch latency), thresholds (usefulness <5 → flag, injection precision <70% → flag), escalation (file issue with skill-evolution team). | Phases 1-3 complete | post-launch-monitoring.md exists with monitoring plan |
| 5.16 | **Final review:** Verify all 11 checklist items pass; verify all 15 files exist in `agents/architect/`; verify all 3 leader files updated; verify shared context updated; verify smoke test plan + monitoring plan documented. Write summary commit message. | All Phase 5 tasks | Final review passes; commit message drafted |

---

## Coupling

- **Tight with:** All Phases 1-4 (Phase 5 verifies the outputs of every prior phase).
- **Loose with:** Project-level convention documents (`docs/agent-prompt-writing-guide.md`) — checklist items reference this guide.
- **Independent of:** Other agent files (no other agents' files are modified in this phase).

---

## Risks (Phase 5 Specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| P5-R1 | **Checklist item 1 false positives.** The forbidden tokens may legitimately appear in fenced code blocks (e.g., tools_note.md code examples showing `load_skill="..."`). | Low | High | Task 5.2 excludes fenced code blocks from the hit count. Manual review for borderline cases. |
| P5-R2 | **Skill injection precision degrades silently.** If 8 skills start competing for the same trigger keywords, no check in Phase 5 catches it. | Medium | Medium | Task 5.15 documents the post-launch monitoring plan; the degradation is detected post-launch, not pre-launch. |
| P5-R3 | **Existing tests fail due to leader integration changes.** Modifying leader meta.json or workflow.md may trigger test fixtures that hardcode the team_members array. | Medium | Low | Task 5.12 runs the existing test suite; if failures occur, the integration changes need adjustment. Mitigation: most tests are integration tests that don't hardcode team_members. |
| P5-R4 | **Smoke test plan is too vague to be actionable.** If smoke-test.md is just "test that it works", it's not implementable later. | Low | Medium | Task 5.13 specifies steps, expected output, pass/fail criteria — concrete enough to implement later. |
| P5-R5 | **memory.md grows unbounded.** If calibration examples accumulate without curation, memory.md becomes noise. | Low | Medium | Task 5.1 limits initial memory.md to 6 sections with 2-3 calibration examples (not a free-form log). Future evolution via `experience()` is structured. |

---

## Exit Criterion

Phase 5 (and the entire plan) is DONE when ALL of the following are true:

- [ ] `agents/architect/memory.md` exists with all 6 sections (council trigger checklist, mode selection defaults, competitive fan-out calibration, skill selection heuristics, known failure modes, calibration examples).
- [ ] Convention checklist items 1-11 all pass (tasks 5.2-5.12).
- [ ] Skill-selection cross-check (task 5.17) passes: 0 hits of `load_skill="architecture-strategy"` in workflow.md; all skill-name references between workflow.md and skill-set.yaml are consistent.
- [ ] Smoke test plan documented in `smoke-test.md`.
- [ ] `.agents/shared/context.md` updated with architect entry.
- [ ] Post-launch monitoring plan documented in `post-launch-monitoring.md`.
- [ ] All 15 architect files present in `agents/architect/`.
- [ ] All 3 leader files updated correctly.
- [ ] Commit message drafted summarizing the entire implementation.

**The architect agent is ready for end-to-end use.**
