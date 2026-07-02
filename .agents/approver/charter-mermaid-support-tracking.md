# Charter + Mermaid Support — Approval Tracking

Current Plan: Charter Agent + Mermaid Chart Support
Slug: charter-mermaid-support

---

## Iteration 001

**Date**: 2026-07-02
**Verdict**: REJECTED

### Blocking Issues

1. **Test file updates missing from plan** — Plan modifies 7 meta.json files (6 agents + leader) but does not include test file updates. At minimum 2 test files will hard-fail:
   - `tests/test_innate_skills_refactoring.py:58-64, 73, 97` — exact innate_skills assertions for developer, reviewer, tester, planner, tidier, approver
   - `tests/test_spawn_team_members.py:408-416, 428, 442` — exact team_members assertions for leader, developer, planner
   - Expected: Plan file list should include test file updates
   - Found: No test files in the 19-file change list

2. **Duplicate agentColorMap not updated** — `message-input.component.ts:69-74` has identical 4-entry `agentColorMap`. Plan only mentions `chat-interface.component.ts`.
   - Expected: Both agentColorMap instances updated
   - Found: Only chat-interface.component.ts in plan

### Non-Blocking Observations

- `darkMode` IS a valid MermaidConfig option (line 260 of ngx-markdown.d.ts) — plan's W4 claim that it's invalid is factually wrong, though chosen approach (theme:'dark') is still correct
- `accent-blue` is used in schedule-card.component.scss (3 places) — plan claims it's "unused" but this is a pre-existing agent color definition, not a collision with charter's use
- mmdc subprocess needs cleanup/timeout/concurrency handling (address during implementation)
- Charter meta.json omits "time" tool (may be intentional)
- Plan lists 19 files but actual count is higher (2+ test files + message-input.component.ts)

---

## Iteration 002

**Date**: 2026-07-02
**Verdict**: APPROVED

### Blocking Issues from Iteration 001 — All Resolved

1. **B1 (Test files) — RESOLVED**: Tasks 10-11 added with line-specific update instructions for both test files. D11 decision record added. Both files in modified files list. Success criterion "All existing tests pass" added. Sorted display format edge case correctly identified.

2. **B2 (Duplicate agentColorMap) — RESOLVED**: Task 5 updated to modify BOTH `chat-interface.component.ts` and `message-input.component.ts`. New context section added. D6 updated with S2+B2 fix.

### Notes from Iteration 001 — All Corrected

- Note 1 (darkMode): D7 corrected — acknowledges darkMode IS valid, justifies theme:'dark' choice
- Note 2 (accent-blue): D6 corrected — "not used by any existing agent" with SCSS usage acknowledged
- Note 3 (time tool): Added to charter meta.json, D12 decision record added

### No New Blocking Issues Found

- File count: 22 files (5 new + 17 modified) — internally consistent
- Core mechanisms unchanged from iteration 001 (all verified)
- Plan is self-consistent, complete, and feasible
