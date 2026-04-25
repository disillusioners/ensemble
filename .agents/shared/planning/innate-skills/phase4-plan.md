# Phase 4: Verify & Cleanup

## Objective

Verify that the refactoring produces byte-for-byte identical system prompts for all agents, remove the now-dead per-agent `skills/` directories, and update/expand the test suite.

## Coupling

- **Depends on**: Phase 2 (meta.json with `innate_skills`), Phase 3 (loader code changes)
- **Coupling type**: tight (verifies output of Phases 2 and 3, removes files Phase 1 made redundant)
- **Shared files with other phases**: All agent directories (removing `skills/`), `tests/test_loader.py`
- **Shared APIs/interfaces**: None
- **Why this coupling**: Must wait for full implementation before verification can succeed

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Capture baseline prompts | Before any changes, run the system and capture the full system prompt for all 12 agents. Save as reference files. | `/tmp/innate-skills-baseline/*.txt` |
| 2 | Verify identical output | After Phases 1-3, capture prompts again and `diff` against baseline. Every agent must produce identical output. | All agent configs + loader |
| 3 | Remove old `skills/` directories | Delete `skills/` from: coder, reviewer, tester, planner, tidier, approver, leader, jober (8 directories) | `agents/*/skills/` |
| 4 | Update existing tests | Update `TestLoadAgentSkills` and `TestComposeSystemPromptWithSkills` to test both innate-skills and legacy paths | `tests/test_loader.py` lines 235-366 |
| 5 | Add new tests | Add tests for: innate-skills loading, backward compat, `find_skill()` with innate-skills, cache invalidation | `tests/test_loader.py` |
| 6 | Final regression run | Run full test suite to confirm no breakage | `tests/` |

## Verification Procedure

### Step 1: Capture Baseline (before changes)

For each of the 12 agents, call `compose_system_prompt()` and save output:
```python
# Pseudo-code for baseline capture
for agent_id in all_agents:
    prompt = compose_system_prompt(agent_id)
    save(f"/tmp/innate-skills-baseline/{agent_id}.txt", prompt)
```

Agents to verify (all 12):
| Agent | Has Skills | Expected Skills Content |
|-------|-----------|------------------------|
| coder | opencode | 220-line opencode skill |
| reviewer | opencode | 220-line opencode skill |
| tester | opencode + test-pack | 220 lines + 86 lines |
| planner | opencode | 220-line opencode skill |
| tidier | opencode | 220-line opencode skill |
| approver | opencode | 220-line opencode skill |
| leader | coordination | 54-line coordination skill |
| jober | job-orchestration | 232-line job-orchestration skill |
| giter | *(none)* | No skill content |
| _mother | *(none)* | No skill content |
| _inner_soul | *(none)* | No skill content |
| _baby_template | *(none)* | No skill content |

### Step 2: Diff After Changes

```bash
for agent in coder reviewer tester planner tidier approver leader jober giter _mother _baby_template; do
    diff /tmp/innate-skills-baseline/${agent}.txt /tmp/innate-skills-after/${agent}.txt
done
# All diffs must be empty
```

### Step 3: Cleanup

Remove these directories (only after verification passes):
```
agents/coder/skills/
agents/reviewer/skills/
agents/tester/skills/
agents/planner/skills/
agents/tidier/skills/
agents/approver/skills/
agents/leader/skills/
agents/jober/skills/
```

## Test Updates

### Existing Tests to Update

**`TestLoadAgentSkills`** (lines 235-296):
- `test_load_agent_skills_multiple` — Add variant with `innate_skills` in meta
- `test_load_agent_skills_empty_dir` — Verify returns empty when no innate_skills and no skills/
- `test_load_agent_skills_skips_non_dirs` — Verify skips non-existent skill names in innate_skills
- `test_load_agent_skills_skips_missing_skill_md` — Verify handles missing skill.md in innate-skills

**`TestComposeSystemPromptWithSkills`** (lines 299-366):
- `test_compose_with_skills` — Add variant using innate-skills loading
- `test_compose_with_base_skill_and_skills` — Verify root `skill.md` + innate-skills compose correctly
- `test_compose_skill_content_preserved` — Verify content matches byte-for-byte

### New Tests to Add

| Test | Purpose |
|------|---------|
| `test_innate_skills_loads_from_central_dir` | Verify loads from `agents/innate-skills/` when `innate_skills` present |
| `test_innate_skills_fallback_to_legacy` | Verify falls back to `skills/` when `innate_skills` absent |
| `test_innate_skills_sorted_alphabetically` | Verify skills are sorted regardless of declaration order |
| `test_innate_skills_missing_skill_warns` | Verify graceful handling when declared skill not found |
| `test_find_skill_with_innate_skills` | Verify `find_skill()` resolves from innate-skills registry |
| `test_cache_invalidation_innate_skills` | Verify cache invalidates when innate-skill file changes |
| `test_full_prompt_identical` | End-to-end: compose with old and new methods, assert identical |

## Key Files

- `tests/test_loader.py` — Main test file (742 lines)
- `agents/*/skills/` — Directories to remove after verification

## Constraints

- **Zero tolerance for prompt differences**: Any diff = blocker. Investigate and fix before proceeding.
- **Tests must cover both paths**: New innate-skills path AND legacy fallback path
- **Don't remove `skills/` until verification passes**: Keep as rollback option

## Deliverables

- [ ] All 12 agents produce byte-for-byte identical system prompts vs baseline
- [ ] All 8 per-agent `skills/` directories removed
- [ ] Updated existing tests pass
- [ ] New tests added and passing
- [ ] Full test suite green
- [ ] No references to old `skills/` path remain in active code (only in backward-compat fallback)
