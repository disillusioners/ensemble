# Plan Overview: Innate-Skills Architecture Refactoring

## Objective

Refactor the agent skill system from per-agent duplicated `skills/` directories into a centralized `agents/innate-skills/` directory with a registry in `meta.json`. The result must produce **IDENTICAL system prompts** for every agent — this is a pure structural refactoring with zero behavioral change.

## Scope Assessment

**MEDIUM** — Affects ~15 files across 3 concern areas (skill files, meta.json configs, loader code). The changes are well-bounded: one new directory, 8 meta.json edits, one Python module modification, and test updates. Risk is mitigated by the identical-output constraint — we can diff system prompts before/after.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Current state**: 6 agents each hold a byte-for-byte identical copy of `opencode/skill.md` (220 lines). 2 other skills (`coordination`, `job-orchestration`, `test-pack`) are unique but still per-agent.

## Current Architecture

```
agents/
├── coder/skills/opencode/skill.md          (220 lines, DUPLICATED ×6)
├── reviewer/skills/opencode/skill.md       (220 lines, DUPLICATED ×6)
├── tester/skills/opencode/skill.md         (220 lines, DUPLICATED ×6)
├── tester/skills/test-pack/skill.md        (86 lines, unique)
├── planner/skills/opencode/skill.md        (220 lines, DUPLICATED ×6)
├── tidier/skills/opencode/skill.md         (220 lines, DUPLICATED ×6)
├── approver/skills/opencode/skill.md       (220 lines, DUPLICATED ×6)
├── leader/skills/coordination/skill.md     (54 lines, unique)
├── jober/skills/job-orchestration/skill.md (232 lines, unique)
├── giter/                                  (no skills)
├── _mother/                                (no skills, system agent)
├── _inner_soul/                            (no skills, system agent)
├── _baby_template/                         (no skills, template)
└── project-experience.md
```

**4 distinct skills:**
| Skill | Lines | Used By |
|-------|-------|---------|
| `opencode` | 220 | coder, reviewer, tester, planner, tidier, approver |
| `coordination` | 54 | leader |
| `job-orchestration` | 232 | jober |
| `test-pack` | 86 | tester |

**Loader step ④** (`load_agent_skills` in `daemon/loader.py` lines 188-211): scans `agent_dir/skills/*/skill.md`, returns `dict[str, str]` sorted alphabetically by directory name.

**`find_skill()`** in `daemon/registry.py` lines 292-311: scans all agents' `skills/<name>/skill.md` to find which agents have a skill.

## Target Architecture

```
agents/
├── innate-skills/
│   ├── opencode/skill.md           (220 lines, single copy)
│   ├── coordination/skill.md       (54 lines)
│   ├── job-orchestration/skill.md  (232 lines)
│   └── test-pack/skill.md          (86 lines)
├── leader/meta.json    ← "innate_skills": ["coordination"]
├── coder/meta.json     ← "innate_skills": ["opencode"]
├── reviewer/meta.json  ← "innate_skills": ["opencode"]
├── tester/meta.json    ← "innate_skills": ["opencode", "test-pack"]
├── planner/meta.json   ← "innate_skills": ["opencode"]
├── tidier/meta.json    ← "innate_skills": ["opencode"]
├── approver/meta.json  ← "innate_skills": ["opencode"]
├── jober/meta.json     ← "innate_skills": ["job-orchestration"]
├── giter/meta.json     ← (no innate_skills field, no skills)
└── ... (system agents unchanged)
```

> **Safeguard (W8)**: The registry's `discover()` method already skips directories that lack a `meta.json` file. Since `innate-skills/` has no `meta.json`, it will never be discovered as a fake agent. No additional guard needed.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Create innate-skills directory | Extract all 4 skills to centralized location | None | — | 30min |
| 2 | Update agent configs | Add `innate_skills` field to all 8 agent meta.json files | Phase 1 (needs skill names) | loose | 20min |
| 3 | Modify loader & registry | Update `load_agent_skills`, `find_skill`, cache keys, backward compat | Phase 1, Phase 2 | tight | 1.5h |
| 4 | Verify & cleanup | Diff system prompts, remove old skills/, update tests | Phase 2, Phase 3 | tight | 1h |

### Coupling Assessment

| From → To | Coupling | Reasoning |
|-----------|----------|-----------|
| Phase 1 → Phase 2 | **loose** | Phase 2 only needs skill names (known upfront), not the actual files |
| Phase 1 → Phase 3 | **tight** | Phase 3 code references `agents/innate-skills/` paths created in Phase 1 |
| Phase 2 → Phase 3 | **tight** | Phase 3 loader reads `innate_skills` from meta.json — must have the field populated |
| Phase 2 → Phase 4 | **loose** | Phase 4 reads meta.json but doesn't modify code |
| Phase 3 → Phase 4 | **tight** | Phase 4 verifies Phase 3's output is identical and removes old code |

**Parallelism opportunity**: Phases 1 and 2 can run in parallel (loose coupling). Phase 3 must wait for **both** Phase 1 and Phase 2. Phase 4 must wait for Phases 2 and 3.

```
Phase 1 ──┬──→ Phase 3 ──→ Phase 4
Phase 2 ──┘────↗
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| System prompts differ after refactoring | **HIGH** — breaks the entire constraint | Low | Capture baseline prompts before, diff after. Write automated test. |
| Cache invalidation breaks (stale skills served) | **MEDIUM** — agents use outdated skill content | Medium | Update cache key construction to track `innate-skills/` file mtimes |
| `find_skill()` breaks for API consumers | **MEDIUM** — runtime lookup fails | Low | Update `find_skill()` to check both old and new paths during migration. **Note (W7)**: `find_skill()` has no production callers currently — only test mocks reference it. Refactoring it is low-risk but still required for correctness. |
| Baby template or spawned agents reference old skills path | **LOW** — spawned agents have no skills currently | Low | Verify `_baby_template` has no skills; note in code for future |
| Alphabetical ordering changes | **MEDIUM** — prompt order differs | Low | Innate-skills loading uses same `sorted()` as current; verify with diff |

## Success Criteria

- [ ] All 4 skill files exist in `agents/innate-skills/` with identical content to originals
- [ ] All 8 agent `meta.json` files have correct `innate_skills` arrays
- [ ] `load_agent_skills()` loads from `agents/innate-skills/` when `innate_skills` field present
- [ ] Backward compatible: agents without `innate_skills` field still load from old `skills/` path
- [ ] System prompts are **byte-for-byte identical** before and after for all 12 agents
- [ ] `find_skill()` correctly resolves skills from both old and new paths
- [ ] Cache invalidation works for `innate-skills/` file changes
- [ ] All per-agent `skills/` directories removed
- [ ] All tests pass (existing + new)

## Tracking

- Created: 2026-04-24
- Last Updated: 2026-04-24 (reviewer feedback: C1-C3 fixes, W4-W8 warnings addressed)
- Status: draft
