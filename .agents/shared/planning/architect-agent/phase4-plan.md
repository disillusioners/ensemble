# Phase 4: Leader Integration

## Objective

Wire the new architect agent into the leader's dispatch surface so that the leader can spawn the architect when architectural decisions arise. After this phase:

- `agents/leader/meta.json` includes `"architect"` in the `team_members` array.
- `agents/leader/soul.md` includes a row for the architect in the team table (lines 81-95 area).
- `agents/leader/workflow.md` includes 3 invocation points: (a) Planning Workflow (after planner, before reviewer), (b) Domain Routing (architecture-decision routing case), (c) Debug Phase 1.5 (architectural-cause classification).

This phase depends only on Phase 1's `agent_id="architect"` decision. It can run in parallel with Phases 2 and 3 because it references only the agent as an entity, not its internal skill content or workflow details.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 4.1 | **Inspect current `agents/leader/meta.json`** at the `team_members` line (currently around line 16 per dispatch). Verify the array structure (JSON array of strings). Note current members. | none (research) | Confirmed current `team_members` array contents |
| 4.2 | **Add `"architect"` to `agents/leader/meta.json` `team_members` array.** Preserve JSON validity. Place in workflow-order position: **after `"approver"`, before `"tester"`** (the leader's team_members array follows workflow phase ordering, not alphabetical). Use edit_file with exact-string match on the existing line. | 4.1 | `team_members` array includes `"architect"`; JSON still valid; no other fields changed |
| 4.3 | **Inspect current `agents/leader/soul.md` team table** (lines 81-95 area per dispatch). Verify the table structure: header row, existing agent rows. Note the row format (e.g., `| **agent-id** | description | when to use \|`). | none (research) | Confirmed current team table format and existing rows |
| 4.4 | **Add architect row to `agents/leader/soul.md` team table.** New row: `\| **architect** \| Solution-architecture specialist — enriches plans with architectural depth, answers hard architecture questions, explores multiple solution approaches via competitive fan-out \| Planning workflow (after planner, before reviewer) when plans need architectural depth; Domain Routing when task involves architecture changes or hard design questions; Debug Phase 1.5 when BIG+ bug may have architectural cause \|`. Place in **workflow-order position: after `approver`, before `tester`** (the leader's team table follows workflow phase ordering, not alphabetical). | 4.3 | Team table has new architect row; markdown table syntax preserved |
| 4.5 | **Inspect current `agents/leader/workflow.md`** and locate the 3 insertion zones: (a) Planning Workflow (around lines 114-116), (b) Domain Routing (around lines 198-220), (c) Debug Phase 1.5 (around lines 424-445). Verify section names exist; re-resolve exact line numbers since they may have drifted. | none (research) | All 3 insertion zones located by section name (line numbers may have drifted) |
| 4.6 | **Add architect invocation at Planning Workflow insertion point.** Insert after the planner produces a plan and before the reviewer (or as a parallel branch). Pattern: `Optional architectural enrichment: planner → architect → reviewer. Invoke architect when (a) plan scope is BIG+, (b) plan touches architectural decisions (persistence, messaging, framework selection, new patterns), or (c) leader/user explicitly requests. Architect writes architecture-recommendation.md to .agents/shared/planning/<feature>/.` Document the conditional trigger criteria (resolves open question OQ-2). | 4.5 | Planning Workflow zone has new architect invocation paragraph; conditional criteria documented |
| 4.7 | **Add architect invocation at Domain Routing insertion point.** New routing case in the domain-routing section: `Architecture decision detected (new persistence layer, new pattern, framework selection, cross-system change, "should we use X or Y?") → route to architect for design BEFORE developer implementation.` Add trigger phrases the leader should recognize. | 4.5 | Domain Routing zone has new architect routing case; trigger phrases listed |
| 4.8 | **Add architect invocation at Debug Phase 1.5 insertion point.** New investigator case for BIG+ multi-system bugs: `Architect as investigator: map the architecture around the failure path (alongside planner's failure-path mapping). Use when the bug involves cross-system architectural boundaries, suspicious coupling, or unclear component ownership.` | 4.5 | Debug Phase 1.5 zone has new architect investigator case |
| 4.9 | **Verify leader integration completeness.** grep `"architect"` in `agents/leader/meta.json` (1 hit in team_members array), `agents/leader/soul.md` (1 hit in team table), `agents/leader/workflow.md` (≥3 hits at the 3 invocation zones). Total ≥5 references. | 4.2, 4.4, 4.6, 4.7, 4.8 | ≥5 references across 3 leader files; no syntax breakage |
| 4.10 | **Rollback procedure (if integration fails).** If the leader integration causes issues (e.g., leader fails to start, team_member validation errors, JSON corruption), revert the 3 leader files to their pre-change state: `git checkout -- agents/leader/meta.json agents/leader/soul.md agents/leader/workflow.md`. The architect agent files under `agents/architect/` are self-contained and can remain — only the 3 leader files need reverting to restore leader functionality. | 4.2, 4.4, 4.6, 4.7, 4.8 | Rollback procedure documented with exact git revert command |

---

## Coupling

- **Tight with:** Phase 1 (leader integration references the agent id from meta.json).
- **Loose with:** Phase 2 (leader workflow.md may reference architect's capabilities, but Phase 4 does not require Phase 2's specific patterns).
- **Independent of:** Phase 3 (leader integration does NOT reference individual skill names; only the architect as an agent). This means Phase 4 can run in parallel with Phase 3.

---

## Risks (Phase 4 Specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| P4-R1 | **JSON syntax breakage in meta.json.** If a comma is misplaced when adding `"architect"`, the array becomes invalid. | High | Low | Task 4.2 uses edit_file with exact-string match (preserves existing commas). Verify with `python -c "import json; json.load(open('agents/leader/meta.json'))"` after edit. |
| P4-R2 | **Table row format mismatch in soul.md.** If the new row doesn't match the column count or alignment, markdown rendering breaks. | Medium | Medium | Task 4.4 specifies the row format explicitly with the same column structure as neighboring rows. Visually inspect after edit. |
| P4-R3 | **Workflow.md line numbers drifted.** The dispatch cites specific line numbers; actual file may have shifted. | Medium | High | Task 4.5 instructs re-resolving by section name (NOT line number). Editor should grep for section headers, not rely on dispatch's line numbers. |
| P4-R4 | **Architect enrichment becomes a latency tax.** If always-run after planner (per OQ-2), SMALL/MEDIUM scope plans get slower. | Medium | Medium | Task 4.6 specifies CONDITIONAL invocation (BIG+ scope OR architectural decisions OR explicit request). Reduces latency for small scope. Matches Approver gating pattern. |
| P4-R5 | **Domain Routing overlap with existing cases.** If architect routing case overlaps with existing planner/reviewer routing, leader may dispatch the wrong agent. | Medium | Medium | Task 4.7 lists specific trigger phrases (architecture decision, new pattern, framework selection) that DON'T overlap with planner (functional scope) or reviewer (quality review). |
| P4-R6 | **Debug Phase 1.5 routing confuses bug-cause classification.** If "architectural cause" is too broad, leader may invoke architect on every BIG+ bug. | Low | Medium | Task 4.8 narrows to "cross-system architectural boundaries, suspicious coupling, unclear component ownership" — specific symptoms, not catch-all. |

---

## Exit Criterion

Phase 4 is DONE when ALL of the following are true:

- [ ] `agents/leader/meta.json` `team_members` array includes `"architect"`.
- [ ] `agents/leader/meta.json` is still valid JSON.
- [ ] `agents/leader/soul.md` team table has a new row for the architect.
- [ ] `agents/leader/workflow.md` has new architect invocation at Planning Workflow insertion (with conditional trigger criteria).
- [ ] `agents/leader/workflow.md` has new architect routing case at Domain Routing insertion.
- [ ] `agents/leader/workflow.md` has new architect investigator case at Debug Phase 1.5 insertion.
- [ ] grep finds ≥5 references to `"architect"` across the 3 leader files.
- [ ] No syntax breakage in any of the 3 modified leader files.
- [ ] Rollback procedure documented (task 4.10) with exact `git checkout` command for the 3 leader files

**Next phase unlock:** Phase 5 verification can begin. The architect is now spawnable by the leader (assuming Phases 1-3 are also complete).
