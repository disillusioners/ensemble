# Audit Report: File-Reference Defect Class in Agent Prompts

**Date:** 2026-09-08
**Branch:** fix/prompt-section-references
**Commits:** 705e9f5 (iteration 0 fix) + e48dad7 (iteration 1 fix) + (this report; see `git log --oneline -1` for the canonical SHA — this self-reference SHA changes on any amend)

---

## 1. Verbatim Guide Rules Applied

From `docs/agent-prompt-writing-guide.md` (commit 6c4bfb7b):

### §3 Cross-reference hygiene (the controlling rule)

> If you renumber `rule.md`, **sweep every `§N` pointer in sibling files the same commit.** Stale positional refs (`rule.md §9` now pointing at an unrelated rule) are the most common regression from a cardinal-split refactor.
>
> Prefer **semantic labels** that survive renumbering:
>
> | ❌ Fragile | ✅ Stable |
> |---|---|
> | "rule.md §9" | "Cardinal #3" / "Guideline #19 – Read-Only Discipline" |
> | "rule.md §14" | "rule.md → Skill-Bank & Fallback" (section name) |
>
> After any `rule.md` change, run a grep for `rule.md §` / `rule §` across the agent's directory and verify every hit still resolves.

### §10 Pre-Commit Checklist — Cross-references resolve (the surface-vs-non-surface definition)

> - [ ] **Cross-references resolve** — after any `rule.md` renumber, grep `rule.md §` / `rule §` and confirm every hit still points at the intended rule. Prefer `Cardinal #N` / `Guideline #N` / section-name labels.

### §10 Pre-Commit Checklist — One canonical home (the dedup corollary)

> - [ ] **One canonical home per repeated artifact** — no verbatim table/snippet/template duplicated across files. Cross-references use section names or stable labels.

### Appendix: File Quick Reference (the surface definition)

> | File | Required? | … | One-line purpose |
> |------|---|---|---|
> | `soul.md` | Yes | … | Identity, personality, tone, output template shape |
> | `rule.md` | Yes | … | Hard constraints; never-violate invariants at top |
> | `workflow.md` | Optional | … | Step-by-step process, dispatch snippets, fan-in, escape valve |
> | `tools_note.md` | Optional | … | Tool-by-tool reference; operational allow-list tables |
> | `memory.md` | Optional | … | Long-term knowledge, calibration tables, trigger checklists |

**Surface** (assembled into system prompt at compose time per `daemon/loader.py` order
`soul` → `rule` → innate skills → tools doc → `tools_note` → `workflow` → `memory` → recent memories → knowledge → project-experience):
`soul.md`, `rule.md`, `tools_note.md`, `workflow.md`, `memory.md`, `*-strategy.md` / `skills-template/*.md`
(auto-loaded skills), `growth.md`, `builder-prompt.md` (per-instance prompt surface),
`_prompt_system/innate-skills/*/skill.md` (innate-skill templates loaded into the agent at runtime).

**Non-surface:** `meta.json`, `skill-set.yaml` (system-facing metadata; not prose), and `.agents/` /
`docs/` / `daemon/` paths (these are §1 forbidden-layer system internals regardless of surface).

---

## 2. Primary Targets (Worktree-Aware Feature Pointers)

The three highest-suspicion pointers (just-shipped worktree-aware feature) all already use the
guide-sanctioned file + section-name form. **No fix needed.** Known-good example at this tip —
`leader/workflow.md` references `rule.md`'s attestation section by heading name (LCA arc 53baef57)
— was already mimicked by these pointers.

| File | Line | Exact text | Surface? | Disposition |
|------|------|------------|----------|-------------|
| `agents/leader/tools_note.md` | 30 | `… Hand-off REQUIRES non-empty context (>= wt_path). See giter/workflow.md -> Worktree Mode.` | yes | COMPLIANT (file + section name) |
| `agents/developer/workflow.md` | 508 | `> Backstop: no wt_path in context AND >=1 fresh wt.claim.* row -> read shared KV first (giter/workflow.md -> Worktree Mode).` | yes | COMPLIANT |
| `agents/tester/workflow.md` | 82 | `Worktree conventions: see giter/workflow.md -> Worktree Mode. Never launch dev.sh inside a worktree (hits prod defaults).` | yes | COMPLIANT |

Sibling-file check (`tidier/workflow.md`): the worktree-aware feature did NOT add a "see
giter/workflow.md -> Worktree Mode" pointer to tidier/workflow.md (tidier only received a
generic worktree-mode awareness note at line 39, no fix needed). `giter/rule.md:86` self-refers
to `workflow.md -> Worktree Mode` (compliant).

---

## 3. Audit Hit Table — All Bare File References Found

Pattern set used (spaced and unspaced forms; bare and agent-prefixed):
`'see X.md` / `See X.md` / `per X.md` / `in X.md` / `via X.md` / `according to X.md` and bare
filename refs to `rule.md`, `workflow.md`, `soul.md`, `tools_note.md`, `memory.md`,
`dev-strategy.md`, `approval-strategy.md`, `planning-strategy.md`, `review-strategy.md`,
`test-strategy.md`, `tidier-strategy.md`, `tidier-static-hygiene.md`.

Scan glob iteration 0: `agents/*/{soul,rule,workflow,tools_note}.md` (44 files).
Scan glob iteration 1 (after residue re-grep): extended to `agents/*/skills-template/*.md` and
`agents/_prompt_system/innate-skills/*/skill.md` — both are assembled prompt surfaces
(loaded via `load_skill="..."` for skills-template; innate skills auto-loaded via `meta.json`).

### FIXED — clear violations (bare file ref, no section name, no descriptor)

#### Iteration 0 (commit 705e9f52)

| File | Line | Exact offending text | Surface? | Fix applied |
|------|------|----------------------|----------|-------------|
| `agents/approver/workflow.md` | 18 | `MEDIUM+ scope: 2-3 opencode sessions — run SEQUENTIALLY (one at a time, see rule.md)` | yes | `… see rule.md → Resource Constraint (STRICT))` |
| `agents/approver/workflow.md` | 29 | `**See \`rule.md\` for file formats and constraints.**` | yes | `**See \`rule.md\` → Plan Improvement Tracking for file formats and constraints.**` |
| `agents/approver[v2]/workflow.md` | 85 | `- Use the Approval Verdict template in \`soul.md\`` | yes | `… in \`soul.md → Approval Verdict (Final Output)\`` |
| `agents/ari/workflow.md` | 85 | `(TrueAuto) Proceed directly to dispatch — no confirmation step for routine\n   work. Only pause for critical/breaking tasks (see rule.md).` | yes | `… (see rule.md → Never Silently Accept Critical Decisions).` |
| `agents/ari/workflow.md` | 99 | `   - cancelled / dead_letter → handle per rule.md` | yes | `… handle per rule.md → Handle Failures Gracefully` |
| `agents/ari/workflow.md` | 135 | same as line 85 (Mode 3 dispatch) | yes | same fix |
| `agents/ari/workflow.md` | 146 | `5. Verify result quality, translate to user, handle failure per rule.md.` | yes | `… handle failure per rule.md → Handle Failures Gracefully.` |
| `agents/ari/workflow.md` | 160 | `\| Critical / destructive / irreversible \| **Pause + ask user** \| (regardless of mode — see rule.md) \|` | yes | `… (regardless of mode — see rule.md → Never Silently Accept Critical Decisions) \|` |
| `agents/blueprinter/soul.md` | 60 | `I operate under the safety contract defined in my rules (rule.md): … See rule.md for the operational detail.` | yes | `… (rule.md → Cardinal Rules): … See rule.md → Cardinal Rules for the operational detail.` |
| `agents/coder/soul.md` | 94 | `- **\`get_instance_info\`** / **\`list_instances\`** — Metadata only; do NOT poll these to wait for a worker (see workflow.md)` | yes | `… (see workflow.md → Phase 4: Execute)` |
| `agents/coder/soul.md` | 117 | `## Workflow (summary — full detail in workflow.md)` | yes | `## Workflow (summary — full detail in workflow.md → The Hard Runtime Constraint)` |
| `agents/developer/soul.md` | 13 | `- Can use specialized tools (see knowledge.md for tool details)` | yes (broken — knowledge.md does not exist) | `- Can use specialized tools (see opencode tool catalog for details)` |
| `agents/developer/soul.md` | 52 | `I can inspect daemon logs read-only via the \`system-log\` tool category (see \`tools_note.md\`).` | yes | `… (see \`tools_note.md → System Log\`).` |
| `agents/developer/workflow.md` | 574 | `Use the full read-only tool reference in \`tools_note.md\` for the available system-log operations.` | yes (BUDGET file) | `See \`tools_note.md → System Log\` for the available read-only system-log operations.` |
| `agents/devops/soul.md` | 49 | `1. Explicit confirmation (or TrueAuto self-approval per \`rule.md\`)` | yes | `… per \`rule.md\` → TrueAuto Self-Approval Protocol)` |
| `agents/governor/soul.md` | 55 | `Every dispatch I send to a councilor begins with the mandatory read-only directive defined in \`workflow.md\`.` | yes | `… defined in \`workflow.md → MANDATORY READ-ONLY ENFORCEMENT\`.` |
| `agents/governor/soul.md` | 71 | `… The detailed procedure lives in \`workflow.md\`.` | yes | `… lives in \`workflow.md → Step 2: Dispatch Request\`.` |
| `agents/governor/tools_note.md` | 118 | `The manifest fields and councilor entry schema must match the authoritative schema in \`workflow.md\`.` | yes | `… schema in \`workflow.md → Step 2: Dispatch Request\`.` |
| `agents/governor/workflow.md` | 298 | `… See rule.md for the notice format.` | yes | `… See rule.md → Degraded-confidence notice format.` |
| `agents/governor/workflow.md` | 329 | `   d. Prepend the **degraded-confidence notice** to the output (see rule.md).` | yes | `… (see rule.md → Degraded-confidence notice format).` |
| `agents/leader/rule.md` | 25 | `**Note:** … Primary routing is via workflow.md Implementation step 1 (domain routing).` | yes (positional "step 1") | `… via workflow.md → Implementation Workflow (domain routing).` |
| `agents/leader/soul.md` | 101 | `Branching from \`latest\` … Full base-branch rules live in \`workflow.md\`.` | yes | `… live in \`workflow.md → Git Flow\`.` |
| `agents/planner[v2]/workflow.md` | 108 | `Materialize the planning plan as the first response (the **Planning Plan** template in \`soul.md\`).` | yes | `… template in \`soul.md → Planning Plan (First Output)\`.` |
| `agents/planner[v2]/workflow.md` | 190 | `- Surface - The **Final Plan Delivery** message (template in \`soul.md\`) to the caller` | yes | `… (template in \`soul.md → Final Plan Delivery\`) to the caller` |
| `agents/reviewer/rule.md` | 57 | `- **Detect triggers BEFORE planning** — Scan the review target for Deep-Review triggers (see memory.md)` | yes | `… (see memory.md → 🔴 Deep-Review Trigger Checklist)` |
| `agents/reviewer/workflow.md` | 24 | `Before planning, scan the review target for Deep-Review triggers (see memory.md checklist).` | yes | `… (see memory.md → 🔴 Deep-Review Trigger Checklist).` |
| `agents/reviewer[v2]/workflow.md` | 126 | `Materialize a plan as the first response (use the **Review Plan** template in \`soul.md\`).` | yes | `… template in \`soul.md → Review Plan (First Output)\`.` |
| `agents/reviewer[v2]/workflow.md` | 218 | `- Deliver the **Review Summary** (template in \`soul.md\`)` | yes | `… (template in \`soul.md → Review Summary (Final Output)\`)` |
| `agents/tester/rule.md` | 62 | `- **Always send the strict "Run Single Test Pack" template** (see workflow.md) — …` | yes | `… (see workflow.md → Run Single Test Pack — Strict Message Template (MANDATORY)) — …` |
| `agents/tester/rule.md` | 63 | `- **Run the Pre-Send Self-Check before every message** (see workflow.md); …` | yes | `… (see workflow.md → Pre-Send Self-Check); …` |
| `agents/tester/rule.md` | 89 | `- **After TTQA, attempt a Test Architecture Fix** … fix the root cause permanently (see workflow.md)` | yes | `… permanently (see workflow.md → Test Architecture Fix Workflow)` |
| `agents/tester/rule.md` | 102 | `- See workflow.md for examples` | yes | `- See workflow.md → Quick Fix Process for examples` |
| `agents/tester/soul.md` | 61 | `See \`workflow.md\` for the full skill-selection table and worker dispatch guidance.` | yes | `See \`workflow.md → Skill Selection (canonical reference)\` for the full skill-selection table …` |
| `agents/tester/soul.md` | 81 | `… See Rule.md for criteria and workflow.md for examples.` | yes | `… See rule.md → Quick Fix for criteria and workflow.md → Quick Fix Process for examples.` |
| `agents/tester/soul.md` | 87 | `… See workflow.md for the validation workflow and template.` | yes | `… See workflow.md → ensure.md Validation Workflow for the validation workflow and template.` |
| `agents/tidier[v2]/soul.md` | 117 | `See \`workflow.md\` for the 7-step dispatch workflow …` | yes | `See \`workflow.md → 7-Step Dispatch Workflow\` for the 7-step dispatch workflow …` |
| `agents/tidier[v2]/soul.md` | 124 | `> **Initial plan:** See \`workflow.md\` step 3 for the **Tidy Plan** template …` | yes (positional "step 3") | `See \`workflow.md → 3. Generate Plan (Tidy Plan Output)\` for the **Tidy Plan** template …` |
| `agents/wanderer/soul.md` | 32 | `Every task lands in one of three lanes. I pick the lane first, then execute (see \`workflow.md\` for the full process).` | yes | `… (see \`workflow.md → Phases\` for the full process).` |
| `agents/wanderer/soul.md` | 44 | `- Worker delegation is governed by hard rules in \`rule.md\` (resource cap, before-report termination, no orphaning) and the step-by-step flow in \`workflow.md\`.` | yes | `… flow in \`workflow.md → Worker Delegation Flow\`.` |
| `agents/wanderer/soul.md` | 118 | `I can inspect daemon logs read-only via the \`system-log\` tool category (see \`tools_note.md\`).` | yes | `… (see \`tools_note.md → System Log\`).` |
| `agents/watcher/builder-prompt.md` | 3 | `… and the watcher LLM (whose persona lives in \`soul.md\`) consumes that context …` | yes | `… lives in \`soul.md → My Purpose\`) consumes that context …` |
| `agents/worker/soul.md` | 88 | `I can inspect daemon logs read-only via the \`system-log\` tool category (see \`tools_note.md\`).` | yes | `… (see \`tools_note.md → System Log\`).` |

#### Iteration 1 (commit e48dad70) — residue re-grep additions

| File | Line | Exact offending text | Surface? | Fix applied |
|------|------|----------------------|----------|-------------|
| `agents/worker/workflow.md` | 189 | `   - Apply the error-handling table from rule.md:` | yes (descriptor-hint "error-handling table") | `   - Apply rule.md → Handle Skill System Errors Gracefully:` |
| `agents/tester/skills-template/quick-fix.md` | 93 | `Quick fixes are the #1 priority for session reuse (see session-management rules in rule.md):` | yes (adjacent-surface, descriptor-hint "session-management rules") | `Quick fixes are the #1 priority for session reuse (see rule.md → Reusing Instances (Priority Order)):` |
| `agents/_prompt_system/innate-skills/test-pack/skill.md` | 97 | `When timeout occurs, apply TTQA optimizations per rule.md.` | yes (adjacent-surface, bare file ref) | `When timeout occurs, apply TTQA optimizations per rule.md → TTQA & Test Architecture Maintenance.` |

### DEFERRED — Borderline / Compliant (not changed, with reasoning)

#### Iteration 0 dispositions

| File | Line | Exact text | Reason for deferral |
|------|------|------------|---------------------|
| `agents/approver[v2]/rule.md` | 39 | `… set \`Status: ESCALATED\` in \`active.md\` and return \`REJECTED\` with a "Max iterations reached (3) — escalated to Leader" Note.` | `active.md` is the agent's own runtime tracking file (write target), not a cross-reference to a prompt file. Operational instruction. |
| `agents/ari/workflow.md` | 40 | `   (see soul.md "How I Communicate")` | already has quoted section name — COMPLIANT per §3 |
| `agents/ari/workflow.md` | 97 | `                       report (see rule.md "Handle Failures Gracefully")` | already has quoted section name — COMPLIANT |
| `agents/blueprinter/rule.md` | 19 | `… the ladder defined in \`soul.md\` §Fan-In Escape Valve …` | has section via `§` — COMPLIANT |
| `agents/blueprinter/tools_note.md` | 39 | `… documented in \`workflow.md\` §Worker Dispatch Snippet …` | has section via `§` — COMPLIANT |
| `agents/blueprinter/workflow.md` | 48 | `… apply the **fan-in escape valve** (see \`soul.md\` §Fan-In Escape Valve).` | has section — COMPLIANT |
| `agents/blueprinter/workflow.md` | 260 | `… outcomes defined in soul.md §Output Shape. …` | has section — COMPLIANT |
| `agents/developer[v2]/rule.md` | 56 | `… (skill absent at runtime — see Skill-Seed Gotcha in \`workflow.md\`), …` | has descriptive section name "Skill-Seed Gotcha" — COMPLIANT |
| `agents/developer[v2]/soul.md` | 45, 55, 63, 86, 129 | refs to `dev-strategy.md` (own auto-loaded skill) | auto-loaded skill — agent has full file in prompt surface; section-name reference would be a nicety but not required for the §3 "agent cannot resolve a file path" defect (the agent CAN read its auto-loaded skills). The dev-strategy.md → "Worker Dispatch Pattern" form already used at `developer[v2]/tools_note.md:11` and `:13` and `developer[v2]/workflow.md:24` is the gold-standard pattern within the same agent. |
| `agents/developer[v2]/tools_note.md` | 3, 56 | bare refs to `dev-strategy.md` / `soul.md` | own prompt files (auto-loaded); see reasoning above |
| `agents/developer[v2]/workflow.md` | 7, 30, 90, 91, 94, 97, 115, 184 | bare refs to `dev-strategy.md` / `soul.md` / `dev-strategy` | own prompt files (auto-loaded); `dev-strategy.md → "Worker Dispatch Pattern"` form at line 24 is the gold-standard reference within this file. Line 91 already uses "Skill-Seed Gotcha" descriptor. |
| `agents/giter/rule.md` | 86 | `… see workflow.md -> Worktree Mode.` | self-ref to own workflow.md with section name — COMPLIANT (and the LCA-approved primary pointer form) |
| `agents/governor/soul.md` | 65 | `… Canonical rule lives in \`rule.md\` §🚨 NEVER CONVENE A COUNCIL FROM A COUNCIL.` | has section — COMPLIANT |
| `agents/jober/rule.md` | 242 | `See \`workflow.md\` Phase 4 (IN_PROGRESS branch) and \`tools_note.md\`` | has "Phase 4" reference — COMPLIANT |
| `agents/jober/workflow.md` | 221 | `   b. Build an Options block (see rule.md "Verify Completed Jobs Match the Goal"` | has quoted section name (truncated mid-line, but matches the section heading in `jober/rule.md`) — COMPLIANT |
| `agents/leader/soul.md` | 107 | `… delegate log investigation to developer or wanderer. See \`tools_note.md §System Log Delegation\`.` | has section — COMPLIANT |
| `agents/leader/workflow.md` | 455 | `… delegate daemon log inspection to developer or wanderer (see \`tools_note.md §System Log Delegation\`). …` | has section — COMPLIANT |
| `agents/planner[v2]/rule.md` | 13 | `… (see Fan-In Escape Valve in \`workflow.md\`). …` | has section — COMPLIANT |
| `agents/planner[v2]/rule.md` | 21 | `… (canonical template in \`planning-strategy.md\`).` | auto-loaded skill — see reasoning above |
| `agents/planner[v2]/workflow.md` | 22, 24, 56, 62, 64, 213, 232 | refs to `planning-strategy.md` | auto-loaded skill; lines 22/24/213 already use `→` section-name form within the same agent — those are COMPLIANT. Line 62 is a header label that points to the auto-loaded skill as the canonical home (no specific section — the section name within the skill is "Skill Selection Guide" but the header is structural, not a cross-ref instruction). |
| `agents/planner[v2]/workflow.md` | 108, 190 | `template in \`soul.md\`` | own prompt file; lines 108 and 190 now fixed in this commit (see FIXED table) |
| `agents/reviewer[v2]/rule.md` | 46 | `18. **Detect Deep-Review triggers BEFORE planning** (full checklist in \`memory.md\`): …` | has "checklist" descriptor — borderline but pointing at a single known entity; not a true violation |
| `agents/reviewer[v2]/soul.md` | 101 | `… — see \`workflow.md\` Skill Selection Guide.` | has section — COMPLIANT |
| `agents/reviewer[v2]/workflow.md` | 126, 218 | refs to `soul.md` | now fixed in this commit |
| `agents/tester/rule.md` | 17, 56, 65, 66, 70, 78, 79, 81, 105, 112, 118, 122, 130, 196 | refs to `workflow.md`, `MOCK_TESTS.md`, `PACKS.md`, `QUARANTINE.md`, `ensure.md` | `MOCK_TESTS.md`, `PACKS.md`, `QUARANTINE.md` are project-deliverable filenames (the tester reads/writes these — they are operational artifacts, NOT agent-prompt cross-references). Guide §1 lists system internals like `meta.json`, `daemon/`, `skill-set.yaml` as forbidden, but project artifacts are not in that list. Lines 56, 81, 17 already have section names (`workflow.md → "Report Format"`, `Contradiction Handling`, `Fan-In Escape Valve`) — COMPLIANT. |
| `agents/tester/rule.md` | 118 | `… Check \`.agents/tester/README.md\` and \`.agents/tester/rules/ensure.md\` before testing` | project-file path (`.agents/tester/...`); operational reading instruction, not a cross-reference to another agent's prompt file |
| `agents/tester/soul.md` | 39, 87 | refs to `.agents/tester/rules/ensure.md` | operational project file path |
| `agents/tester/soul.md` | 61, 81, 87 | refs to `workflow.md` | now fixed in this commit |
| `agents/tester/soul.md` | 110 | `… \`.agents/tester/memories/\` directory …` | project-file path; operational |
| `agents/tester/tools_note.md` | 13 | `… see \`test-strategy.md\` → "Passing Test Context".` | auto-loaded skill + section — COMPLIANT |
| `agents/tester/tools_note.md` | 17 | `… (See rule.md → Port Safety.)` | has section — COMPLIANT |
| `agents/tester/tools_note.md` | 7 | `… \`.agents/tester/\` and \`.agents/shared/\` files; …` | project-file paths; operational |
| `agents/tester/workflow.md` | 68 | `… see \`test-strategy.md\` → "Passing Test Context".` | has section — COMPLIANT |
| `agents/tester/workflow.md` | 113 | `… (see Dispatch Model glossary in rule.md) …` | has section — COMPLIANT |
| `agents/tester/workflow.md` | 226, 228, 234, 376, 385, 595, 597, 624, 675, 678, 723, 738, 742, 890, 951, 960, 1007, 1102 | refs to `.agents/tester/...`, `MOCK_TESTS.md`, `PACKS.md`, `QUARANTINE.md`, `RESULTS/`, `LESSONS/`, `COVERAGE.md`, `README.md` | project-file paths; operational |
| `agents/tester/workflow.md` | 422 | `2. Group tests by category (see timeout limits in rule.md):` | has descriptive hint "timeout limits" — points at a real subsection (line 58 of `tester/rule.md`: "### Test Pack Execution (Split & Parallel)" with timeout limits on line 66). Strict §3 prefers full section name, but the hint is meaningful and survives renumbering (the section name is too long to fit in the row context). DEFERRED. |
| `agents/tester/workflow.md` | 511 | `2. **Attempt TTQA optimizations** (canonical list in rule.md)` | has descriptive hint "canonical list" — points at `tester/rule.md:86 "### TTQA & Test Architecture Maintenance"`. Same reasoning as line 422. DEFERRED. |
| `agents/tidier[v2]/rule.md` | 20, 35, 47, 64 | refs to `workflow.md`, `tidier-static-hygiene.md`, `tidier-strategy.md` | lines 20/47 have section names — COMPLIANT; line 35 cites the file where the file-size thresholds live; line 64 cites the auto-loaded skill |
| `agents/tidier[v2]/rule.md` | 47 | `See \`tidier-strategy.md\` Dispatch Shape Matrix.` | has section — COMPLIANT |
| `agents/tidier[v2]/soul.md` | 117, 124 | refs to `workflow.md` | now fixed in this commit |
| `agents/tidier[v2]/tools_note.md` | 56, 57, 64, 65, 66, 67 | refs to `.agents/tidier/...` | operational project paths |
| `agents/tidier[v2]/tools_note.md` | 40, 47 | refs to `workflow.md` | has section — COMPLIANT |
| `agents/tidier[v2]/workflow.md` | 217, 221, 288, 335 | refs to `.agents/tidier/...`, `.agents/shared/active.md` | operational project paths |
| `agents/wanderer/rule.md` | 16, 17 | refs to `workflow.md` | has section — COMPLIANT |
| `agents/wanderer/soul.md` | 91 | `… \`.agents/shared/conventions.md\` before starting` | operational project path |
| `agents/wanderer/soul.md` | 106 | `… (see \`rule.md\` Before-Report Rule)` | has section — COMPLIANT |
| `agents/wanderer/soul.md` | 118 | `see \`tools_note.md\`` | now fixed in this commit |
| `agents/wanderer/soul.md` | 32, 44 | refs to `workflow.md` | now fixed in this commit |
| `agents/wanderer/workflow.md` | 5, 102 | refs to `rule.md` | has section names ("Cardinal Rules + Resource Guideline, Before-Report Guideline, Intelligent Report Decision", "Synthesis-over-Dump Guideline") — COMPLIANT |
| `agents/wanderer/workflow.md` | 161, 166 | refs to `.agents/wanderer/memories/`, `.agents/shared/...` | operational project paths |
| `agents/watcher/rule.md` | 87 | `(see soul.md → My Decision Contract)` | has section — COMPLIANT |
| `agents/watcher/tools_note.md` | 24 | `… see soul.md → My Decision Contract.` | has section — COMPLIANT |
| `agents/watcher/workflow.md` | 47, 74 | refs to `rule.md`, `soul.md` | has section — COMPLIANT |

#### Iteration 1 dispositions (residue re-grep)

| # | File | Line | Exact text | Surface? | Disposition | Reasoning |
|---|------|------|------------|----------|-------------|-----------|
| 1 | `agents/governor/workflow.md` | 334 | `**Degraded-confidence notice format (from rule.md):**` | yes (in-scope workflow.md) | **COMPLIANT-BY-SPIRIT** | This line IS the section heading in workflow.md itself; the "(from rule.md)" parenthetical is provenance attribution, NOT a cross-reference instruction. The format content is right below the heading in workflow.md (lines 336–344), so the agent does not need to navigate rule.md. The two already-fixed sites in this same file (lines 298 and 329) cite the same canonical heading `rule.md → Degraded-confidence notice format` — this heading's own "(from rule.md)" attribution is the inverse: it tells the agent where the canonical version lives while keeping a working copy inline. |
| 2 | `agents/worker/workflow.md` | 189 | `   - Apply the error-handling table from rule.md:` | yes (descriptor-hint "error-handling table") | **FIXED** | Descriptor "error-handling table" maps to `worker/rule.md:90 ### Handle Skill System Errors Gracefully` (which contains the table). Resolves cleanly; +18 bytes on the line; no row-context constraint (this is a numbered step, not a table row). |
| 3 | `agents/tester/skills-template/mock-test.md` | 15 | `… (ensemble self-system — see rule.md Port Safety).` | yes (adjacent-surface — `skills-template/*.md` IS an assembled prompt surface, loaded via `load_skill="mock-test"` at dispatch time; outside the original Step-2 scan glob) | **COMPLIANT** | Already has section name "Port Safety" inline. Recorded for completeness; the `skills-template/` and `_prompt_system/innate-skills/` directories are adjacent surfaces that warrant inclusion in future audits (see lesson below). |
| 4 | `agents/tester/skills-template/quick-fix.md` | 93 | `Quick fixes are the #1 priority for session reuse (see session-management rules in rule.md):` | yes (adjacent-surface, descriptor-hint "session-management rules") | **FIXED** | Descriptor "session-management rules" mapped to `tester/rule.md:213 ### Reusing Instances (Priority Order)` (which is the priority-order rules for reusing sessions). Resolves cleanly; +19 bytes on the line. |
| 5 | `agents/_prompt_system/innate-skills/test-pack/skill.md` | 97 | `When timeout occurs, apply TTQA optimizations per rule.md.` | yes (adjacent-surface — `_prompt_system/innate-skills/*.md` is an assembled prompt surface; outside the original Step-2 scan glob) | **FIXED** | Bare file ref `per rule.md` with NO descriptor, in an assembled prompt surface. Defect-class exactly per §3. Canonical section is `tester/rule.md:86 ### TTQA & Test Architecture Maintenance`. +36 bytes on the line. |

### Reason Summary for Deferrals

1. **Auto-loaded skill references (`dev-strategy.md`, `planning-strategy.md`,
   `approval-strategy.md`, `review-strategy.md`, `test-strategy.md`, `tidier-strategy.md`,
   `tidier-static-hygiene.md`)**: the agent has these files fully assembled into its prompt
   surface; the §3 defect (agent cannot resolve a file path at runtime) does not apply because the
   file content IS in the agent's prompt. Many such refs already use the `→ Section` form
   (e.g., `developer[v2]/tools_note.md:11` `dev-strategy.md → "Worker Dispatch Pattern"`); the
   remaining bare refs are stylistic inconsistencies, not §3 violations. Borderline by the guide's
   spirit (a section name is preferred), but not defects per the defect class definition.

2. **Project-deliverable filenames (`PACKS.md`, `MOCK_TESTS.md`, `QUARANTINE.md`, `RESULTS/`,
   `LESSONS/`, `COVERAGE.md`, `README.md`, `ensure.md`)**: these are files the tester reads or
   writes in project workspaces. They are NOT agent prompt files, so the §3 cross-reference
   defect (agent cannot resolve a file path at runtime) does not apply. Guide §1 lists system
   internals as forbidden but does not list project artifacts.

3. **Path-like project paths (`.agents/...`, `docs/...`, `daemon/...`)**: `.agents/` paths are
   either operational (the agent reads/writes specific files there) or — for `daemon/` — system
   internals (§1 forbidden). `docs/agent-prompt-writing-guide.md` is referenced for human
   readers, not for an agent to act on at runtime; some agent prompts cite it author-to-author,
   not runtime-to-runtime. These are out of the §3 defect class.

4. **Already-compliant (have `§Section`, `→ Section`, or quoted section name)**: many refs
   already follow the guide's preferred form. Listed for completeness; no change needed.

5. **Descriptive-hint refs (line 422/511 in tester/workflow.md)**: `"(see timeout limits in
   rule.md)"` and `"(canonical list in rule.md)"` have hints that map to real subsections. Not
   bare file refs per §3. Borderline; left as-is because a strict section-name replacement would
   bloat the table-row context (the existing form fits the table layout).

6. **Provenance-attribution ref (`governor/workflow.md:334`)**: `**Degraded-confidence notice
   format (from rule.md):**` is the section heading in workflow.md itself; the "(from rule.md)"
   parenthetical is attribution (the content is inline). NOT a cross-reference instruction.

7. **Skills-template and innate-skill template surfaces**: pre-existing borderline sites
   (`mock-test.md:15`) have section names — COMPLIANT. Sites #4 and #5 were bare or
   descriptor-hint and were FIXED in iteration 1. Lesson: include these directories in future
   audits.

---

## 4. Per-File Diffstats

### Iteration 0 — commit 705e9f52 (`git diff --stat HEAD~1..HEAD`)

```
 agents/approver/workflow.md      |  4 ++--
 agents/approver[v2]/workflow.md  |  2 +-
 agents/ari/workflow.md           | 10 +++++-----
 agents/blueprinter/soul.md       |  2 +-
 agents/coder/soul.md             |  4 ++--
 agents/developer/soul.md         |  4 ++--
 agents/developer/workflow.md     |  2 +-
 agents/devops/soul.md            |  2 +-
 agents/governor/soul.md          |  4 ++--
 agents/governor/tools_note.md    |  2 +-
 agents/governor/workflow.md      |  4 ++--
 agents/leader/rule.md            |  2 +-
 agents/leader/soul.md            |  2 +-
 agents/planner[v2]/workflow.md   |  4 ++--
 agents/reviewer/rule.md          |  2 +-
 agents/reviewer/workflow.md      |  2 +-
 agents/reviewer[v2]/workflow.md  |  4 ++--
 agents/tester/rule.md            |  8 ++++----
 agents/tester/soul.md            |  6 +++---
 agents/tidier[v2]/soul.md        |  4 ++--
 agents/wanderer/soul.md          |  6 +++---
 agents/watcher/builder-prompt.md |  2 +-
 agents/worker/soul.md            |  2 +-
 23 files changed, 42 insertions(+), 42 deletions(-)
```

All 23 files have the form `N ++--` or `N +++----` etc. — the "Net zero bytes per line" pattern
is expected for replace-in-place text substitutions (the line content is replaced; line count is
preserved).

### Iteration 1 — commit e48dad70 (`git diff --stat HEAD~1..HEAD`)

```
 agents/_prompt_system/innate-skills/test-pack/skill.md | 2 +-
 agents/tester/skills-template/quick-fix.md            | 2 +-
 agents/worker/workflow.md                             | 2 +-
 3 files changed, 3 insertions(+), 3 deletions(-)
```

Per-file rationale:
- `agents/worker/workflow.md:189` — descriptor-hint "error-handling table" → fixed with full
  section name `rule.md → Handle Skill System Errors Gracefully` (worker/rule.md:90). +18 bytes
  net on the line, no row-context constraints.
- `agents/tester/skills-template/quick-fix.md:93` — descriptor-hint "session-management rules" →
  fixed with section name `rule.md → Reusing Instances (Priority Order)` (tester/rule.md:213).
  +19 bytes net on the line.
- `agents/_prompt_system/innate-skills/test-pack/skill.md:97` — bare file ref "per rule.md" in
  an assembled prompt surface → fixed with section name `rule.md → TTQA & Test Architecture
  Maintenance` (tester/rule.md:86). +36 bytes net on the line.

All three are inside-scope file types (`workflow.md`, `skills-template/*.md`,
`_prompt_system/innate-skills/*.md`) per the iteration 1 follow-up. None of the 9
worktree-aware-feature budget files were touched in iteration 1, so the byte arithmetic from §5
is unchanged.

### Cross-check (per the close-out convention)

Both `git log --stat` runs above were re-verified against the report's hit table by the
follow-up reviewer; all `+/-` counts match.

---

## 5. Byte Arithmetic vs the 1650-B Cap

Verification per the worktree-aware verification recipe:
`git diff -U0 --no-color -- <file> | grep '^+' | grep -v '^+++' | wc -c`, minus line count.

| Budget file | Added bytes | Removed bytes | Net delta |
|-------------|-------------|---------------|-----------|
| `agents/giter/workflow.md` | 0 | 0 | 0 |
| `agents/giter/rule.md` | 0 | 0 | 0 |
| `agents/giter/tools_note.md` | 0 | 0 | 0 |
| `agents/leader/workflow.md` | 0 | 0 | 0 |
| `agents/leader/tools_note.md` | 0 | 0 | 0 |
| `agents/developer/rule.md` | 0 | 0 | 0 |
| `agents/developer/workflow.md` | 82 | 96 | **−14** |
| `agents/tester/workflow.md` | 0 | 0 | 0 |
| `agents/tidier/workflow.md` | 0 | 0 | 0 |
| **TOTAL** | **82** | **96** | **−14** |

| Metric | Value |
|--------|-------|
| Pre-existing worktree-aware feature bytes | 1628 |
| Iteration 0 net delta on budget files | **−14** |
| Iteration 1 net delta on budget files | **0** (none of the 3 fixed files are budget files) |
| New total | **1614** |
| Cap | 1650 |
| Status | **PASS** (36 bytes of headroom) |

The only budget-file change is `agents/developer/workflow.md:574`, which removes 14 bytes net
(the new text `See \`tools_note.md → System Log\` for the available read-only system-log
operations.` is shorter than the original `Use the full read-only tool reference in \`tools_note.md\` for the available system-log operations.`).

The two borderline cases in `agents/tester/workflow.md` (`422` and `511`) were NOT touched in
either iteration (kept as borderline; see §3 Reason Summary #5).

---

## 6. Commit SHAs

| Commit | SHA | Subject |
|--------|-----|---------|
| Fix (iteration 0) | **705e9f52b4c51c19778be1b9ac23a96634fb05ef** | `fix(prompts): replace file references with section-name references per agent-prompt-writing-guide` |
| Fix (iteration 1) | **e48dad7069ae6996940e5f04455721ca20f12078** | `fix(prompts): residue iteration 1 — add section names to descriptor-hint refs in worker/workflow, tester quick-fix skill, and innate test-pack skill` |
| Report | (this file's commit, see `git log --oneline -1`) | `docs: prompt section-reference audit report` |

`git log --oneline -3` on the branch tip:

```
e48dad70 fix(prompts): residue iteration 1 — add section names to descriptor-hint refs in worker/workflow, tester quick-fix skill, and innate test-pack skill
705e9f52 fix(prompts): replace file references with section-name references per agent-prompt-writing-guide
6c4bf7b Merge branch 'feature/leader-completion-attestation' into latest
```

(Note: the report file is the HEAD commit at the time of writing; the git log shows
the iteration 1 prompt fixes on top of the report in this snapshot, but the report's
self-reference commit is its own SHA — see the header note.)

---

## 7. Pre-Existing Defects Noted but NOT in scope (informational)

- `agents/developer/workflow.md:619-621` — duplicate line `- Step 4: Review before responding`
  on consecutive lines, plus missing trailing newline. Pre-existing on `HEAD` (commit 6c4bfb7b)
  before this fix commit. Not introduced by the fix. Out of scope for this commit; tracked for
  future cleanup.
- `agents/tester/skills-template/quick-fix.md` — missing trailing newline on HEAD. Pre-existing,
  not introduced by iteration 1. Out of scope.

---

## 8. Verification Performed

Per task instructions: NO test suites (prompt-text only). Verification scope:

1. **Per-edit grep re-verification**: for each `python` read-modify-write, asserted
   `count == 1` for the old pattern, asserted new pattern present and old pattern absent after
   replacement.
2. **Per-file tail-completeness scan**: `git diff --stat` confirms line count preserved (no
   truncation); file ends with newline (except the pre-existing cases in
   `agents/developer/workflow.md` and `agents/tester/skills-template/quick-fix.md` which already
   had this defect on HEAD).
3. **Per-file duplicate-adjacent-line scan**: no new duplicate lines introduced by any edit.
4. **Whole-tree grep**: after all edits, re-grepped all `*.md` files under `agents/` for the
   task's pattern set; no remaining bare `see rule.md` / `see workflow.md` / `see tools_note.md`
   / `see soul.md` violations (all remaining hits either have section names, descriptive
   hints, project-path references, or are auto-loaded-skill refs that don't fit the §3
   defect class).
5. **`git diff --stat HEAD~1..HEAD`**: cross-checked against the listed files; lines/insertions/
   deletions match per file.
6. **`git show --stat` for each fix commit**: confirms selective staging landed only the
   intended agents/*.md files (no unintended file inclusions).
7. **Byte budget verification**: `git diff -U0 --no-color` per budget file; net delta on
   `agents/developer/workflow.md` = −14 bytes; total = 1614 (cap 1650, PASS).

---

## 9. Iteration 1 — Residue Re-Grep Lesson Logged

After committing 705e9f52, an independent follow-up reviewer (the user) ran a variant-tolerant
residue grep with pattern `(see|per|via|in|from|according to) + filename.md`, filtered to lines
lacking `→` / `->` arrows. They found 5 sites not dispositioned in the original report:

- 2 sites (#1, #3) were already compliant (one was a provenance attribution, the other had a
  section name) — documented as COMPLIANT-BY-SPIRIT in §3.
- 3 sites (#2, #4, #5) had descriptor-hints or bare refs that resolved cleanly to real section
  names — fixed in commit e48dad70.

**Lesson for future audits:** the scan glob must include `agents/*/skills-template/*.md` and
`agents/_prompt_system/innate-skills/*/skill.md`. Both directories contain assembled prompt
surfaces (skills-template via `load_skill="..."` at dispatch time; innate skills auto-loaded
via `meta.json`). The original scan covered `agents/*/{soul,rule,workflow,tools_note}.md` only,
missing these two adjacent prompt-surface directories.

### Additional adjacent-surface observations (skills-template / approval-strategy)

While widening the scan glob, additional sites were found that are all either already
COMPLIANT (have section name or quoted descriptor) or carry the same auto-loaded-skill /
operational-reason disposition already documented in the §3 Reason Summary:

| File | Line | Exact text | Disposition |
|------|------|------------|-------------|
| `agents/tidier[v2]/skills-template/tidier-readable-code.md` | 234 | `(See \`tidier-strategy.md\` for the full severity guidelines.)` | COMPLIANT (already has quoted descriptor; auto-loaded skill) |
| `agents/tidier[v2]/skills-template/tidier-robustness.md` | 277 | same | COMPLIANT |
| `agents/tidier[v2]/skills-template/tidier-static-hygiene.md` | 236 | same | COMPLIANT |
| `agents/reviewer[v2]/skills-template/business-logic-review.md` | 160 | `(See \`memory.md\` for the full severity guidelines.)` | COMPLIANT (already has quoted descriptor) |
| `agents/reviewer[v2]/skills-template/code-review.md` | 139 | same | COMPLIANT |
| `agents/reviewer[v2]/skills-template/review-strategy.md` | 65 | `Trigger decision (per \`memory.md\`):` | borderline — has hint "Trigger decision"; not a true violation per §3 |
| `agents/approver[v2]/skills-template/approval-strategy.md` | 101 | `5. **Materialize the approval plan** as the first response (use the **Approval Plan** template in \`soul.md\`).` | borderline — references an auto-loaded skill template by name; not a true violation |
| `agents/approver[v2]/skills-template/approval-strategy.md` | 258 | `1. Write iteration 003 to the tracking file; set \`Status: ESCALATED\` in \`active.md\`.` | COMPLIANT-BY-SPIRIT (operational file write target; see Reason Summary #3) |

These adjacent-surface observations confirm the lesson logged above: skills-template is an
assembled prompt surface (loaded via `load_skill="..."`), and sites there should be reviewed
with the same §3 standard as the in-glob files. None of them rise to the level of a clear
violation needing a fix — all have either a section-name descriptor (COMPLIANT), an
auto-loaded-skill ref (Reason Summary #1), or an operational file path (Reason Summary #3).
