# Phase 3: Skill Template Cleanup (Frontmatter + Perspective Rewrites + Vocabulary Reframing)

> **📋 v2 changes (reviewer F1 + W3):** Changed Approach A from "vocabulary swap" to "perspective rewrite" for 3 skills. Added `unit-test.md` content redesign assessment (potential deferral). Added W3 test-strategy.md frontmatter clarification. Restructured approach taxonomy: vocabulary-only vs perspective rewrite.

## Objective
Fix the stale `auto_load` frontmatter in 3 skill templates, rewrite planner-perspective skills to executor perspective, and reframe executor-perspective skills to worker-agnostic vocabulary. The goal: when a skill is loaded onto a worker via `load_skill`, its instructions are **conceptually correct** — they address the worker as the executor, not as a planner who delegates further.

## Coupling
- **Depends on**: None
- **Coupling type**: independent (different files from Phase 2 and Phase 4)
- **Shared files with other phases**: none
- **Why this coupling**: Skill templates are standalone prompt fragments injected into a worker's context. They don't depend on soul.md/rule.md being rewritten.

## Context

### The Core Problem (F1): Perspective Mismatch
Skills are loaded onto **workers (executors)**, but several skill templates are written from the **tester's (planner's) perspective**. A worker receiving "You plan and delegate; opencode runs" would think it should plan+delegate, not execute. **Simply changing "opencode" to "worker" doesn't fix the role confusion.**

There are two distinct categories of fix needed:

| Category | Problem | Fix |
|----------|---------|-----|
| **Perspective rewrite** (F1) | Skill is written from planner perspective — addresses someone who plans + delegates. Loaded on a worker who should execute. | Rewrite so instructions address the worker directly: "You are the executor. Here's how to run/investigate X." |
| **Vocabulary-only reframing** | Skill is already written from executor/neutral perspective, but uses "opencode" as the executor name. | Replace "opencode" with neutral executor language or add a disambiguation preamble. |

### Skill Classification (after perspective audit)

| Skill File | Perspective | "opencode" count | Fix Category | Severity |
|------------|-------------|-----------------|--------------|----------|
| `unit-test.md` | **Planner** ("You plan and delegate; opencode runs") | 5 | 🔴 **Content redesign** (see below) | HIGH |
| `mock-test.md` | **Planner** ("You design and document the spec; opencode implements") | 5 | 🔴 **Perspective rewrite** | HIGH |
| `ensure-validation.md` | **Planner** ("map them to test packs, run them through opencode") | 7 | 🔴 **Perspective rewrite** | HIGH |
| `test-pack-execution.md` | **Planner** ("how to actually launch, monitor, fix, report") | 4 | 🔴 **Perspective rewrite** | HIGH |
| `integration-test.md` | Neutral/executor | 1 | 🟡 **Vocabulary-only** | LOW |
| `quick-fix.md` | Neutral/executor | 1 | 🟡 **Vocabulary-only** | LOW |
| `test-strategy.md` | Planner (but correctly — this is tester's own planning skill) | 1 | Review only | LOW |
| `e2e-test.md` | Neutral/executor | 0 | ✅ No changes needed | — |
| `flaky-test-management.md` | Neutral/executor | 0 | ✅ No changes needed | — |

### Frontmatter Drift (3 files)
The seeder (`skill_seed_service.py`) reads `skill-set.yaml` as the source of truth — so `auto_load` in template frontmatter is **dead metadata** (no runtime effect). BUT it is misleading for humans editing the templates. These 3 files have `auto_load: true` in their frontmatter while `skill-set.yaml` correctly marks them `false`:

| File | Frontmatter `auto_load` | skill-set.yaml `auto_load` | Fix |
|------|------------------------|---------------------------|-----|
| `skills-template/test-pack-execution.md` | `true` | `false` | → `false` |
| `skills-template/mock-test.md` | `true` | `false` | → `false` |
| `skills-template/unit-test.md` | `true` | `false` | → `false` |

### W3: test-strategy.md Frontmatter (DO NOT CHANGE)
> **`test-strategy.md` is the ONLY template that should keep `auto_load: true`** — it matches `skill-set.yaml`. Do not change it. This skill is the tester's own auto-loaded planning skill; it is NOT dispatched to workers. Its frontmatter is correct.

---

## Tasks

### Task 1: Fix frontmatter in 3 files (mechanical)
For each of `test-pack-execution.md`, `mock-test.md`, `unit-test.md`:
- Change `auto_load: true` → `auto_load: false` in the YAML frontmatter block (top of file).
- This is a single-line edit per file. No runtime effect, but removes misleading metadata.

> **W3 reminder**: Do NOT touch `test-strategy.md` frontmatter (`auto_load: true` is correct).

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Fix test-pack-execution frontmatter | `auto_load: true` → `false` | skills-template/test-pack-execution.md |
| 2 | Fix mock-test frontmatter | `auto_load: true` → `false` | skills-template/mock-test.md |
| 3 | Fix unit-test frontmatter | `auto_load: true` → `false` | skills-template/unit-test.md |

### Task 2: Perspective Rewrites (3 skills) — F1 Critical

**These skills are written from the tester's (planner's) perspective but get loaded onto workers (executors). The rewrite must change the ADDRESSEE, not just the vocabulary.**

**What "perspective rewrite" means:**
- Current: "You design the spec; opencode implements" (addresses a planner who delegates)
- Target: "You are the executor. Design and implement the mock test spec directly." (addresses the worker who executes)
- Current: "Coordinate unit test execution across packs. You plan and delegate; opencode runs."
- Target: "You are the executor. [scope-appropriate instructions for running/analyzing X]"

**Preserve all domain knowledge:** procedures, templates, heuristics, port rules, sizing tables. Only the addressee + action verbs change.

| # | Skill | Current Perspective Issue | Rewrite Target | Key Files |
|---|-------|--------------------------|----------------|-----------|
| 4 | `mock-test.md` (5 mentions) | "You design and document the spec; opencode implements and executes" | Rewrite to executor: "You are the executor. Design and implement mock tests directly." All "opencode implements" → "implement directly". Port rules, what-to-mock table, decision rule preserved. | skills-template/mock-test.md |
| 5 | `ensure-validation.md` (7 mentions) | "map them to test packs, run them through opencode" + "prepare an opencode task" | Rewrite to executor: "You are the executor. Validate ensure.md requirements directly." All "run through opencode" → "run directly". All "prepare an opencode task" → "execute the validation task". Contradiction handling, 4-phase workflow, pack-mapping preserved. | skills-template/ensure-validation.md |
| 6 | `test-pack-execution.md` (4 mentions) | "how to actually launch, monitor, fix, and report" + "Spawn opencode to organize tests" + "One pack per opencode session" | Rewrite to executor: "You are the executor. Run the test pack directly." All "Spawn opencode" → "organize directly". "One pack per opencode session" → "Execute one pack". Pre-Send Self-Check, TTQA optimization, pack existence gate, output format preserved. | skills-template/test-pack-execution.md |

### Task 3: 🔴 unit-test.md Content Redesign Assessment — F1 Critical

**Problem:** `unit-test.md` is written from planner perspective AND has a **content mismatch** with its dispatch purpose:

| Aspect | Current Template | Dispatch Purpose (test-strategy.md line 78) |
|--------|-----------------|---------------------------------------------|
| Perspective | Planner ("You plan and delegate; opencode runs") | Should be executor |
| Scope | Full 5-step orchestration: discover → delegate → analyze → fix → validate | **"Unit test discovery / coverage analysis (read-only investigation)"** — NO execution |
| Content | Describes planning sessions, delegating to opencode, reusing sessions for fixes | Should describe discovery + coverage documentation only |

**The template describes a full orchestration workflow, but the dispatch table maps `unit-test` to read-only discovery/coverage analysis.** This is a content mismatch, not just a vocabulary issue.

**Assessment required (Task 3a):**
1. Read `unit-test.md` fully and compare against `test-strategy.md` dispatch table entry (line 78).
2. Determine the scope of the mismatch:
   - **Option A (in-scope):** Rewrite `unit-test.md` to match its dispatch purpose — strip the orchestration steps (discover→delegate→fix→validate) and focus on discovery + coverage analysis only. The execution/fix steps belong in `test-pack-execution.md` (which already covers pack execution).
   - **Option B (defer):** If the redesign is too large for this plan (the current template has extensive content: pack sizing heuristics, coverage documentation patterns, fix workflow), defer to a **separate follow-up task**. In this case, for the current plan: fix only the frontmatter + add a TODO note flagging the mismatch for future redesign.
3. **Recommendation:** If the template's domain content (sizing heuristics, coverage patterns) can be preserved while stripping the orchestration framing, Option A is feasible. If it requires splitting into planner-facing vs executor-facing variants, defer (Option B).

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 7 | Assess unit-test.md content mismatch | Compare template scope vs dispatch purpose. Decide: Option A (in-scope rewrite) or Option B (defer). Document decision + rationale. | skills-template/unit-test.md, skills-template/test-strategy.md:78 |
| 8 | (If Option A) Rewrite unit-test.md | Strip orchestration framing; focus on discovery + coverage analysis; preserve sizing heuristics + coverage patterns. Rewrite to executor perspective. | skills-template/unit-test.md |
| 8b | (If Option B) Add TODO note to unit-test.md | Add frontmatter note: `# TODO: Content redesign needed — template describes orchestration but dispatch maps to read-only discovery. See test-strategy.md:78.` Fix frontmatter only. | skills-template/unit-test.md |

### Task 4: Vocabulary-Only Reframing (2 skills) — Low severity

These skills are already written from executor/neutral perspective. They just use "opencode" as the executor name. A disambiguation preamble or light vocabulary swap suffices.

**Approach B — Disambiguation Preamble:**
Add a 2-3 line preamble at the top of the skill body (after frontmatter):
```
> **Execution Context:** This skill may be loaded onto a worker instance via `load_skill`.
> In that context, "opencode" references below mean "you, the worker executor".
> Execute instructions directly rather than delegating further.
```

| # | Task | Approach | Key Files |
|---|------|----------|-----------|
| 9 | Add preamble to integration-test.md (1 mention) | B (preamble) | skills-template/integration-test.md |
| 10 | Add preamble to quick-fix.md (1 mention) | B (preamble) | skills-template/quick-fix.md |

### Task 5: Review test-strategy.md (1 mention) — Low severity

Already has the worker dispatch table. Review the single remaining "opencode" mention and reframe if trivial; otherwise leave (it's the auto_load planning skill, consumed by the tester, not a worker).

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 11 | Review test-strategy.md single "opencode" mention | If the mention is in the dispatch table context (describing opencode as fallback), keep it — it's correct. If it's a stale delegation reference, reframe. | skills-template/test-strategy.md |

## Key Files
- `agents/tester/skills-template/unit-test.md` 🔴 (content redesign)
- `agents/tester/skills-template/mock-test.md` 🔴 (perspective rewrite)
- `agents/tester/skills-template/ensure-validation.md` 🔴 (perspective rewrite)
- `agents/tester/skills-template/test-pack-execution.md` 🔴 (perspective rewrite)
- `agents/tester/skills-template/integration-test.md` 🟡 (preamble)
- `agents/tester/skills-template/quick-fix.md` 🟡 (preamble)
- `agents/tester/skills-template/test-strategy.md` (review only)

**No changes needed for:** `e2e-test.md` (0 mentions), `flaky-test-management.md` (0 mentions).

## Constraints
- **Do NOT change `skill-set.yaml`** — it is already correct. Frontmatter fixes are cosmetic alignment only.
- **Do NOT change `test-strategy.md` frontmatter** (W3) — `auto_load: true` is correct.
- **Do NOT change runtime behavior** — frontmatter is dead metadata (seeder reads yaml). This is purely about prompt correctness + human clarity.
- **Preserve all testing-domain content** in each skill (procedures, templates, heuristics, port rules). Only the addressee + delegation vocabulary changes.
- **Perspective rewrites change the addressee** from "planner who delegates" to "executor who runs" — not just vocabulary swap.
- Skills must read correctly in BOTH contexts: (a) loaded on a worker via `load_skill`, (b) referenced by the tester for planning. Worker-agnostic/executor phrasing satisfies both.
- `e2e-test.md` and `flaky-test-management.md` are already clean — do not touch.

## Deliverables
- [ ] 3 files have `auto_load: false` frontmatter (test-pack-execution, mock-test, unit-test)
- [ ] `test-strategy.md` frontmatter `auto_load: true` UNCHANGED (W3 verified)
- [ ] 3 planner-perspective skills rewritten to executor perspective (mock-test, ensure-validation, test-pack-execution)
- [ ] unit-test.md content mismatch assessed — Option A or B decision documented with rationale
- [ ] (If Option A) unit-test.md rewritten to match dispatch purpose (discovery/coverage only)
- [ ] (If Option B) TODO note added to unit-test.md + decision documented in notes.md
- [ ] 2 low-count skills have disambiguation preamble (integration-test, quick-fix)
- [ ] test-strategy.md reviewed (1 ref handled)
- [ ] grep `opencode` across skills-template → remaining mentions are either zero, contextual/harmless, or correctly describing opencode as fallback
- [ ] No domain content lost (procedures/templates/heuristics/port-rules intact)

## Est. Time: 3-4 hours (includes unit-test.md assessment + potential deferral decision)
