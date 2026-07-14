# Phase 7: Innate Skill Updates

## Objective

Modify the two innate skills that the tester agent depends on to reflect the new two-layer skill model: (1) `test-pack` gains a section referencing the evolvable skills that build on it, and (2) `dynamic-skill` gains an explanation of the auto_load concept.

## Coupling

- **Depends on**: None (content-only changes)
- **Coupling type**: independent code-wise, but tested together with P6 (W5)
- **Shared files with other phases**: `agents/_prompt_system/innate-skills/test-pack/skill.md`, `agents/_prompt_system/innate-skills/dynamic-skill/skill.md`
- **Shared APIs/interfaces**: None — these are markdown content files read by `load_agent_skills()`
- **Why this coupling**: Content changes must be consistent with the skill names defined in P1's `skill-set.md`

## Context

### Current test-pack/skill.md

Contains invariant rules:
- 5-Minute Hard Cap (No Exception)
- Dual-Layer Timeout (Both Required)
- Subprocess-Based Timeout
- Explicit Output (PASS/FAIL/TIMEOUT)
- Predictable Timing

### Current dynamic-skill/skill.md

Contains:
- Overview of skill injection (HumanMessage before user message)
- 6 tool descriptions (skill_search, skill_list, skill_view, skill_create, skill_fix, skill_feedback)
- When to use section
- Good skills section

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add evolvable skills reference to test-pack | New section at the end | `agents/_prompt_system/innate-skills/test-pack/skill.md` |
| 2 | Add auto_load explanation to dynamic-skill | New section about foundational skills | `agents/_prompt_system/innate-skills/dynamic-skill/skill.md` |

## Detailed Changes

### 7.1 test-pack/skill.md — Add Evolvable Skills Reference

**File**: `agents/_prompt_system/innate-skills/test-pack/skill.md`

Append at the END of the file (after "Predictable Timing" section):

```markdown

---

## Evolvable Skills (Built on This Foundation)

The following **evolvable skills** build on the invariant rules above. They are loaded automatically (auto_load) or on-demand when relevant:

| Skill | Load Mode | Builds On |
|-------|-----------|-----------|
| **test-strategy** | auto_load | Blast radius assessment + scope planning |
| **test-pack-execution** | auto_load | Pack creation, TTQA optimization, execution flow |
| **mock-test** | auto_load | Mock service testing methodology |
| **unit-test** | auto_load | Unit test discovery + delegation workflow |
| **integration-test** | on-demand | Cross-component + API boundary testing |
| **e2e-test** | on-demand | Full-flow + browser automation testing |
| **ensure-validation** | on-demand | ensure.md requirement validation |
| **flaky-test-management** | on-demand | Detection, quarantine, un-quarantine |
| **quick-fix** | on-demand | Failures under 20 lines |

These skills evolve over time via A/B testing and feedback. The **invariant rules in this skill (5-min cap, dual-layer timeout, explicit output) NEVER change** — they are the immovable foundation all evolvable skills respect.

If an evolvable skill contradicts an invariant here, **this innate skill wins**.
```

### 7.2 dynamic-skill/skill.md — Add auto_load Explanation

**File**: `agents/_prompt_system/innate-skills/dynamic-skill/skill.md`

Add a new section AFTER the overview paragraph and BEFORE the "## Tools" section:

```markdown

## Two Load Modes

Skills reach you in two ways:

### auto_load (Foundational Skills)
Some skills are **always loaded** into your system prompt before every task. These are foundational methodologies you need to know upfront — they appear in a dedicated section of your prompt. You don't need to search for them or wait for injection; they're already there.

If an auto_load skill needs improvement, use `skill_fix` to request changes. The system evaluates feedback and may evolve the skill via A/B testing.

### On-Demand (Contextual Skills)
Other skills are **injected as needed** — when your current task matches their relevance. These appear as `[System Inject]` messages before the user's message. Use them if they fit, ignore them if they don't.

You can also actively search for skills using `skill_search` when you suspect a relevant procedure exists but wasn't auto-injected.
```

## Key Files

- `agents/_prompt_system/innate-skills/test-pack/skill.md` — invariant rules + evolvable skill reference
- `agents/_prompt_system/innate-skills/dynamic-skill/skill.md` — auto_load explanation

## Constraints

- **Do NOT remove** any existing content from either file — only ADD new sections
- The evolvable skills reference in test-pack must match the skill names from `skill-set.md` (Phase 1)
- The auto_load explanation in dynamic-skill must be consistent with the actual behavior (Phase 5 implementation)
- Keep additions concise — these are in the system prompt, token budget matters

## Test Strategy

No standalone tests needed — innate skill files are markdown content loaded by `load_agent_skills()`. Existing tests for innate skill loading should still pass.

### P6+P7 Integration Test (W5)

Phase 7 content changes are verified via the integration test defined in Phase 6 (`tests/test_tester_skill_chain.py`):
1. System prompt for tester includes updated test-pack content with "Evolvable Skills" section
2. System prompt for tester includes updated dynamic-skill content with "Two Load Modes" section
3. No regression in `tests/test_innate_skills_refactoring.py`

Manual verification:
1. Read composed system prompt for tester → verify both sections present
2. Verify skill names in test-pack reference match `skill-set.md` (P1)

## Deliverables

- [ ] test-pack/skill.md has "Evolvable Skills" section referencing all 9 skills
- [ ] dynamic-skill/skill.md has "Two Load Modes" section explaining auto_load
- [ ] No existing content removed from either file
- [ ] Skill names match skill-set.md (Phase 1)
