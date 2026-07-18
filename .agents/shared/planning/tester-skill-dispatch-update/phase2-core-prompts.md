# Phase 2: Core Prompt Rewrite for Skill-Per-Worker Dispatch

> **📋 v2 changes (reviewer F2 + W2):** Added rule.md line 10 exclusion list (F2), glossary-preamble approach instead of inline repetition (F2), cross-file terminology sync check deliverable (W2), prompt-priority ordering note (F2).

## Objective
Rewrite the tester's 3 core prompt files (`soul.md`, `rule.md`, `tools_note.md`) so an LLM loading them immediately understands the skill-per-worker dispatch model with `load_skill`, rather than the stale opencode-only delegation vocabulary.

## Coupling
- **Depends on**: Phase 1 (backup must exist before overwriting)
- **Coupling type**: tight (same files Phase 1 backs up)
- **Shared files with other phases**: none — Phase 3/4 touch different files
- **Why this coupling**: Prevents data loss. The backup must exist first.

## Prompt Priority Ordering (F2)
The tester's prompt is assembled in this order:
```
soul.md → rule.md → workflow.md → tools_note.md → skills
```
**rule.md (position 2) overrides workflow.md (position 3).** If rule.md and workflow.md use different dispatch terminology, the LLM follows rule.md and ignores workflow.md. Therefore:
- Dispatch terminology in rule.md MUST match workflow.md's phrasing exactly.
- Define terminology ONCE (glossary preamble) rather than repeating inline — avoids drift and 8× word inflation.

## Context
The tester's prompt files were written before the skill-per-worker architecture shipped. They describe the tester as delegating "entirely to opencode sessions" with no awareness of:
- The `worker` agent (0 mentions in rule.md)
- The `load_skill` parameter (0 mentions in soul.md/rule.md/tools_note.md)
- The 1:1 skill-attribution model
- Worker reuse rules

Meanwhile, `meta.json` (already updated) lists `team_members: ["explorer","worker"]` and `skill_injection: true`, and `workflow.md` (already correct) documents the dispatch pattern with 17 mentions each of `load_skill` and `worker`. The soul.md/rule.md/tools_note.md directly contradict both.

**The rewrite must bring soul.md/rule.md/tools_note.md into alignment with meta.json and workflow.md.**

## Guiding Principle for the Rewrite
> **Change the delegation vocabulary, not the testing domain knowledge.**
>
> rule.md contains 15KB of high-value testing rules (blast radius analysis, pack lifecycle, flaky test management, quick-fix eligibility, port safety, ensure.md gates). These MUST be preserved. Only the *how-I-delegate* framing changes: "opencode session executes" → "worker with load_skill executes, opencode is fallback".

## 🔴 F2 Exclusion List — DO NOT MECHANICALLY SWEEP

The following items in rule.md contain **actual tool names** or **runtime lifecycle constraints** that must NOT be find-and-replaced. They must be reviewed individually and either preserved as-is or deliberately reframed.

| Line | Content | Type | Action |
|------|---------|------|--------|
| 10 | `external_opencode_resume_session` | **Tool/function name** | DO NOT change the function name. Verify if the worker session lifecycle also has a 10-min poll limit before reframing the surrounding text. If workers use a different lifecycle, update the timer reference; if same, keep as-is but clarify it applies to opencode sessions specifically. |
| 10 | `10-min opencode-session poll limit` | **Runtime lifecycle constraint** | Verify against worker session lifecycle first (see above). Do NOT blindly replace "opencode-session" with "worker". |
| 10 | `5-min pack-execution cap` | **Runtime constraint (pack timer)** | This is pack-scoped, not session-scoped — keep as-is regardless of executor type. |

**Rule:** Before reframing line 10, check whether `external_opencode_resume_session` and the 10-min poll limit apply to worker instances or only opencode sessions. The worker agent may have a different (or no) session poll limit.

---

## Tasks

### Task 1: Rewrite `agents/tester/soul.md` (4KB)

**Current problems:**
- Line 1-2: "I coordinate testing efforts, delegate work to opencode sessions" — opencode-only framing
- Purpose line: "delegate work through opencode sessions" — no worker mention
- Core Belief #8: "I coordinate, opencode instances execute" — opencode-only
- §"What I Do Directly": "Spawn opencode instances to execute work" — opencode-only
- §"What I Delegate to Opencode Instances": entire section — needs dual-mode
- §"Two Pillars of Testing" → Mock Tests: "opencode implements scripts" — opencode-only

**Required changes (high-level):**

| Section | Current | Target |
|---------|---------|--------|
| Opening description | "delegate work to opencode sessions" | "delegate test execution to skill-equipped worker instances via `load_skill`, with opencode as fallback" |
| Purpose | "delegate work through opencode sessions" | "lead testing, dispatch skill-specific work to workers" |
| Core Belief #8 | "I coordinate, opencode instances execute" | "I coordinate, workers (via load_skill) and opencode execute" |
| §"What I Do Directly" → Spawn bullet | "Spawn opencode instances" | "Spawn worker instances and dispatch skills via `load_skill`; use opencode for infrastructure tasks" |
| §"What I Delegate to Opencode Instances" (rename) | "What I Delegate to Opencode Instances" | **"What I Delegate (Dual-Mode)"** — split into: (a) **Workers** for skill-specific test tasks (unit/mock/integration/e2e/pack/maintenance), (b) **opencode** for infrastructure-only tasks |
| Mock Tests maintainer | "opencode implements scripts" | "workers implement scripts (via mock-test skill)" |

**New content to ADD:**
- A short "Skill-Per-Worker Dispatch" section (5-8 lines) explaining: spawn worker → send_message with `load_skill="<skill>"` → worker gets ONE skill → worker calls skill_feedback → clean attribution.
- Worker reuse rule (1-2 lines): reuse a worker with new `load_skill` if context is still relevant; else spawn fresh.
- A pointer: "See workflow.md for the full skill-selection table and worker-vs-opencode decision matrix."

### Task 2: Rewrite `agents/tester/rule.md` (15KB)

**⚠️ This is the highest-risk file — 15KB of testing rules. Do NOT lose domain knowledge.**

**Current problems:**
- **0 mentions of "worker"** — an LLM loading rule.md has zero awareness workers exist
- 10 mentions of "opencode" framing delegation
- Likely contains rules like "Never read source/test code directly — use opencode sessions" and "opencode sessions execute all code/test/file work"
- No mention of `load_skill` parameter
- No skill-selection guidance (which skill for which test type)
- **Line 10 contains `external_opencode_resume_session` (tool name) and "10-min poll limit" (lifecycle constraint) — see F2 Exclusion List above**

**Required changes (high-level):**

1. **Add a glossary preamble at the TOP of rule.md** (F2 recommendation) — define the dispatch model ONCE, then reference it:
   ```markdown
   ## Dispatch Model (Glossary)

   - **Worker** = primary executor. Dispatched via `spawn_instance(agent="worker")` +
     `send_message(load_skill="<skill>")`. Receives exactly ONE skill. Calls
     `skill_feedback(applied=True/False)` for attribution.
   - **opencode** = infrastructure fallback. Used for tasks with no matching skill
     (standalone bash/file ops). Tool: `external_opencode_resume_session` for long ops.
   - **Tester (me)** = planner + dispatcher. I never execute test code directly.
   ```
   This avoids 8× word inflation from inlining "worker with load_skill (primary) or opencode (fallback)" in every rule occurrence.

2. **Sweep the 10 "opencode" mentions — but CHECK the F2 Exclusion List first:**
   - Lines NOT on the exclusion list: reframe using the glossary terms.
     - "opencode sessions execute all code/test/file work" → "workers (via load_skill) execute skill-specific test work; opencode handles infrastructure-only tasks"
     - "Never read source/test code directly — use opencode sessions" → "Never read source/test code directly — dispatch to a worker with the appropriate `load_skill`, or use opencode for infrastructure tasks"
     - Any "delegate to opencode" → "dispatch to worker with load_skill (primary) or opencode (infrastructure fallback)"
   - Line 10 (exclusion list): DO NOT mechanically replace. Verify worker session lifecycle first (see F2 Exclusion List table above).

3. **Add worker-vs-opencode decision criteria** (compact, can reference workflow.md):
   | Need | Use |
   |------|-----|
   | Skill-specific test execution | Worker + load_skill |
   | Infrastructure-only (standalone bash/file, no skill) | opencode |

4. **Preserve ALL testing-domain rules:** blast radius analysis, pack lifecycle (TTQA, 5-min cap, splitting), flaky detection (3× retry, quarantine), quick-fix eligibility (<20 lines), port safety (8088 never kill, mock ports 10000-19999), ensure.md gates, version control mandate. These are domain knowledge, not delegation vocabulary.

### Task 3: Rewrite `agents/tester/tools_note.md` (1.7KB)

**Current problems:**
- §"Delegated (opencode sessions)": "everything else — running tests, reading/writing source & test code" — opencode-only
- §"opencode Dependency": "My entire execution model depends on it" — no worker awareness
- §"Team Members": lists only `explorer`, omits `worker`
- §"Innate Skills": lists `opencode`, `test-pack`, `todo` — omits `dynamic-skill`

**Required changes (high-level):**

| Section | Current | Target |
|---------|---------|--------|
| §"Direct vs Delegated Access" → Delegated | "Delegated (opencode sessions): everything else" | **Split into two:** (1) **Workers (via load_skill)** — skill-specific test execution: running tests, writing test code, fixes. (2) **opencode** — infrastructure-only tasks (standalone bash/file ops without a skill) |
| §"opencode Dependency" | "My entire execution model depends on it" | Reframe: "opencode is my infrastructure fallback. Skill-specific test execution goes through worker instances via `load_skill`." Keep the "if opencode is down" guidance but scope it to infrastructure tasks. |
| §"Team Members" | Lists only `explorer` | **Add `worker`** — "worker — skill-agnostic executor. Dispatch via `spawn_instance(agent='worker')` + `send_message(load_skill='<skill>')`. One skill per worker. Reuse if context is relevant." |
| §"Innate Skills" | "`opencode`, `test-pack`, `todo`" | Add `dynamic-skill`: "`opencode`, `test-pack`, `todo`, `dynamic-skill` — the **dynamic-skill** skill teaches me about `load_skill` dispatch and `skill_feedback` attribution." |

## Key Files
- `agents/tester/soul.md` (4062 bytes → rewrite)
- `agents/tester/rule.md` (15533 bytes → careful rewrite, preserve domain rules, apply F2 exclusion)
- `agents/tester/tools_note.md` (1703 bytes → rewrite)

## Constraints
- **Do NOT change `workflow.md`** — it is already correct.
- **Do NOT change `meta.json`** — already correct.
- **Do NOT change `skill-set.yaml`** — already correct.
- **Do NOT mechanically sweep F2 Exclusion List items** (rule.md line 10) — verify worker lifecycle first.
- **Preserve all testing-domain rules** in rule.md. Only delegation vocabulary changes.
- Keep file structure (headers, sections) recognizable — don't reorganize rule.md wholesale.
- The `dynamic-skill` innate skill is already in meta.json's `innate_skills` array — tools_note.md should reflect it.
- **Terminology must match workflow.md** (W2) — see deliverable below.

## Deliverables
- [ ] `agents/tester/soul.md` rewritten with dispatch model + dual-mode + worker references
- [ ] `agents/tester/rule.md` rewritten — glossary preamble at top, all non-excluded opencode mentions reframed, new dispatch section added, domain rules preserved
- [ ] `agents/tester/rule.md` line 10 `external_opencode_resume_session` and "10-min poll limit" verified against worker lifecycle (F2) — either preserved as-is or deliberately reframed with evidence
- [ ] `agents/tester/tools_note.md` rewritten — worker in team_members, dual-mode delegated access, dynamic-skill in innate skills
- [ ] grep `worker` in soul.md/rule.md/tools_note.md → each has multiple mentions (target: ≥3 each)
- [ ] grep `load_skill` in soul.md/rule.md/tools_note.md → each has ≥1 mention
- [ ] No testing-domain rules lost from rule.md (manual review)
- [ ] **W2: Dispatch terminology in rule.md matches workflow.md's phrasing exactly** — cross-file consistency check. Compare: (a) "worker" usage, (b) `load_skill` parameter name, (c) "skill_feedback" reference, (d) dual-mode worker-vs-opencode framing. Both files must use identical terms.

## Est. Time: 2.5-3.5 hours
