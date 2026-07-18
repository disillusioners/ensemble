# Plan Overview: Tester Skill-Dispatch Update

> **📋 Revision v2 (2026-07-17)** — Incorporated reviewer findings F1, F2, W1, W2, W3.
> Key changes: skill template perspective rewrites (not just vocab swaps), rule.md line 10 tool-name exclusion + glossary-preamble approach, design-doc count corrected to 13, cross-file terminology sync check added.

## Objective
Update the tester agent's prompt files (soul.md, rule.md, tools_note.md) and skill templates to fully embrace the skill-per-worker dispatch model, replacing stale opencode-only delegation vocabulary. Also fix frontmatter drift and design-doc parameter name.

## Scope Assessment
**MEDIUM** — Touches 3 core prompt files + 8 skill templates + 1 design doc + 1 backup step. All changes are documentation/prompt content (no code, no schema, no migration). Risk is concentrated in Phase 2 (rule.md 15KB) and Phase 3 (3 skills need perspective rewrites, 1 needs content redesign).

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Pattern**: Skill-Per-Worker Architecture (Milestone 2) — already implemented at runtime; only prompts/docs are stale.
- **Created**: 2026-07-17 (v1), Revised: 2026-07-17 (v2)

## Verified Findings (from code grep + reviewer verification)

| Claim | Verified | Evidence |
|-------|----------|----------|
| rule.md has 0 mentions of "worker" | ✅ | `grep -in "worker" → ZERO` |
| rule.md has 10 mentions of "opencode" | ✅ | `grep -ic "opencode" → 10` |
| rule.md line 10 contains `external_opencode_resume_session` tool name + "10-min poll limit" lifecycle constraint | ✅ (F2) | Line 10: `For longer operations, call external_opencode_resume_session to continue past the 10-min opencode-session poll limit` — MUST be excluded from mechanical sweep |
| rule.md is prompt-priority position 2 (after soul.md) — overrides workflow.md and skills | ✅ (F2) | Prompt assembly order: soul.md → rule.md → workflow.md → tools_note.md → skills. Terminology must be consistent across all. |
| soul.md has 8 "opencode" mentions, 0 dispatch-aware "worker" | ✅ | grep confirmed |
| workflow.md already correct (17 "load_skill", 17 "worker") | ✅ | grep confirmed — DO NOT CHANGE |
| tools_note.md omits "worker" from team members | ✅ | Lists only `explorer` |
| skill-set.yaml correct (only test-strategy auto_load:true) | ✅ | Full read — DO NOT CHANGE |
| 3 skill templates have stale `auto_load: true` frontmatter | ✅ | mock-test, test-pack-execution, unit-test |
| test-strategy.md frontmatter `auto_load: true` — CORRECT, do not change | ✅ (W3) | Matches skill-set.yaml. Only test-strategy keeps true. |
| unit-test.md written from PLANNER perspective (5-step orchestration) but dispatch table maps it to "read-only discovery/coverage analysis (no execution)" | ✅ (F1) | Template: "Coordinate... You plan and delegate; opencode runs" + 5 steps. test-strategy.md line 78: "Discovery + coverage documentation (no execution)". Major content mismatch. |
| mock-test.md written from planner perspective ("You design and document the spec; opencode implements") | ✅ (F1) | Line 6 confirmed |
| ensure-validation.md written from planner perspective ("map them to test packs, run them through opencode") | ✅ (F1) | Line 7 confirmed |
| test-pack-execution.md written from planner perspective ("how to actually launch, monitor, fix, report") | ✅ (F1) | Line 6 confirmed |
| Design doc uses `skill="` in **13** places (not 11), 0 `load_skill=` | ✅ (W1) | Re-grep: lines 47-54 (8), 61, 76, 202, 217, 273 = 13 total |
| backup/agents/tester/ does NOT exist | ✅ | ls confirmed |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backup | Snapshot current tester prompts to `backup/agents/tester/` | None | — | 10 min |
| 2 | Core Prompt Rewrite | Rewrite `soul.md`, `rule.md`, `tools_note.md` for worker dispatch + glossary preamble + terminology sync | Phase 1 (backup first) | tight (write after backup) | 2.5-3.5h |
| 3 | Skill Template Cleanup | Fix frontmatter + perspective rewrites (4 skills) + vocab reframing (2 skills) + unit-test content redesign flag | None (independent of 2) | independent | 3-4h |
| 4 | Design Doc Fix | Correct `skill=` → `load_skill=` in design doc (13 occurrences) | None | independent | 15 min |

### Coupling Assessment

- **Phase 1 → Phase 2**: **tight** — Backup must complete before overwriting the 3 core files.
- **Phase 2 ↔ Phase 3**: **independent** — Different files entirely (core prompts vs skill templates). Can run in parallel.
- **Phase 2 ↔ Phase 4**: **independent** — Core prompts vs design doc.
- **Phase 3 ↔ Phase 4**: **independent** — Skill templates vs design doc.

**Recommended scheduling**: Phase 1 → (Phase 2 + Phase 3 + Phase 4 in parallel).

> **⚠️ Note on Phase 2 ↔ Phase 3 conceptual coupling**: While the files are independent, the *dispatch terminology* must be consistent across both (rule.md and skill templates). If run in parallel, agree on terminology FIRST (e.g., "worker", "load_skill", "executor") and apply uniformly. See W2 deliverable in Phase 2.

## Architecture: What the Rewrites Must Convey

### The Dispatch Model (NEW — must be in soul.md/rule.md)
```
1. Tester plans with `test-strategy` (auto_load: true)
2. Tester dispatches skill-specific execution:
     spawn_instance(agent="worker")
     send_message(instance_id=<worker>, message="...", load_skill="<skill-name>")
3. Worker receives exactly ONE skill via <meta>{"load_skill":"<name>"}</meta>
4. Worker executes, calls skill_feedback(applied=True/False)
5. Clean 1:1 attribution: one skill load → one skill_feedback call
```

### Dual-Mode Decision (must replace opencode-only framing)
| Need | Use |
|------|-----|
| Skill-specific test execution (unit/integration/e2e/mock/pack/maintenance) | **Worker** + `load_skill` |
| Infrastructure-only tasks (no skill, e.g., standalone bash/file ops) | **opencode** (fallback) |

### Worker Reuse Rule
A worker can be reused with `send_message` + new `load_skill` IF the worker still has relevant context (same test module, same codebase area). If context is stale/unrelated, spawn a new worker.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Rewrite loses important domain logic from rule.md (15KB of testing rules) | high | Backup first; preserve all testing-domain rules, only change delegation vocabulary |
| Mechanical find-and-replace corrupts tool name `external_opencode_resume_session` and "10-min poll limit" on rule.md line 10 | high (F2) | Explicit exclusion list in Phase 2; verify worker session lifecycle before reframing |
| Terminology drift between rule.md and workflow.md causes LLM confusion (rule.md overrides workflow.md) | high (W2) | Cross-file terminology sync check in Phase 2 deliverables |
| Perspective rewrite of skill templates loses domain content (procedures, templates, heuristics) | medium (F1) | Rewrite instructions address worker directly; preserve all procedural knowledge; only change the addressee from "planner who delegates" to "executor who runs" |
| unit-test.md content redesign is too large for this plan | medium (F1) | Flagged for potential deferral to separate follow-up; current plan scopes the decision |
| Over-rewriting: changing runtime behavior accidentally | high | All files are prompts/docs — seeder reads skill-set.yaml, NOT template frontmatter. Frontmatter fixes are cosmetic. |
| Inconsistency between soul.md/rule.md/tools_note.md after partial edit | medium | Phase 2 edits all 3 files together in one coherent pass + glossary preamble |

## Success Criteria
- [ ] `backup/agents/tester/` contains soul.md, rule.md, tools_note.md (pre-edit copies)
- [ ] `agents/tester/soul.md` describes worker dispatch + load_skill + dual-mode
- [ ] `agents/tester/rule.md` describes worker dispatch + references load_skill (no more "delegate entirely to opencode") + glossary preamble at top
- [ ] `agents/tester/rule.md` line 10 `external_opencode_resume_session` and "10-min poll limit" are UNCHANGED (tool name + lifecycle constraint preserved)
- [ ] `agents/tester/rule.md` dispatch terminology matches workflow.md exactly (W2 sync check)
- [ ] `agents/tester/tools_note.md` lists worker in team_members + dual-mode note + dynamic-skill in innate skills
- [ ] 3 skill templates (mock-test, test-pack-execution, unit-test) have `auto_load: false` in frontmatter
- [ ] test-strategy.md frontmatter `auto_load: true` is UNCHANGED (W3)
- [ ] 4 planner-perspective skills rewritten to executor perspective (mock-test, ensure-validation, test-pack-execution, + unit-test if not deferred)
- [ ] unit-test.md content redesign assessed (split or defer decision documented)
- [ ] Design doc uses `load_skill=` everywhere (13 occurrences fixed)
- [ ] workflow.md and skill-set.yaml are UNCHANGED (already correct)
- [ ] meta.json is UNCHANGED (already correct)

## Do-Not-Touch List (Already Correct)
- `agents/tester/workflow.md` — already documents dispatch pattern correctly
- `agents/tester/skill-set.yaml` — already correct source of truth
- `agents/tester/meta.json` — already has worker in team_members, skill_injection=true
- `agents/tester/skills-template/test-strategy.md` frontmatter `auto_load: true` — matches skill-set.yaml (W3)
- `agents/worker/*` — worker is properly configured
- Runtime code (`instance.py:708-715`, `skill_meta_parser.py`, `skill_seed_service.py`) — fully implemented

## Tracking
- Created: 2026-07-17
- Last Updated: 2026-07-17 (v2 — reviewer findings F1, F2, W1, W2, W3 incorporated)
- Status: draft (v2)
