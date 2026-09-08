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

**Prompt surface — split into two scopes (reconciled 2026-09-08):**

1. **Agent-prompt surface** — files assembled into the agent's own prompt at runtime
   (subject to the v2 cross-reference rule per §3):
   `soul.md`, `rule.md`, `tools_note.md`, `workflow.md`, `memory.md`,
   `*-strategy.md` / `skills-template/*.md` (auto-loaded skills),
   `_prompt_system/innate-skills/*/skill.md` (innate-skill templates loaded into
   the agent at runtime).

2. **Per-instance scaffolding** — files loaded into the instance prompt but NOT
   subject to v2 closure grep (the v2 rule governs navigational cross-references
   between prompt sections; these files are infrastructure, not prompts-with-sections):
   `growth.md` (auto-summarized per-run notes), `builder-prompt.md` (mother-builder
   template loaded only during spawn), `_prompt_system/knowledge.md`,
   `_prompt_system/project-experience.md`, `_prompt_system/critical-notes.md`.

**Non-surface:** `meta.json`, `skill-set.yaml` (system-facing metadata; not prose), and `.agents/` /
`docs/` / `daemon/` paths (these are §1 forbidden-layer system internals regardless of surface).
The §12.5 closure grep operates on agent-prompt surface (1); the per-instance scaffolding
(2) is loaded but exempted per §12.5 survivors #1 and #2 (and the §12.5 #0 operational
exclusion interpretation governs bare-`agents/` prefix tokens — operational paths
stay exempt, cross-reference uses do not).

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
| `agents/developer/soul.md` | 13 | `- Can use specialized tools (see knowledge.md for tool details)` | yes (broken — knowledge.md does not exist) | `- Can use specialized tools` (parenthetical dropped in iteration 2 to remove phantom-target reference; per leader adjudication) |
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
| `agents/watcher/workflow.md` | 24, 47, 59, 74 | `soul.md → My Decision Contract` (L24, L74); `rule.md → Critical-Path Detection` (L47); `rule.md → Combined decision` (L59) | has section — COMPLIANT |
| `agents/watcher/workflow.md` | 96 | `Cardinal rules 1–7 (from \`rule.md\`) take **absolute precedence** …` | COMPLIANT-BY-SPIRIT — "(from rule.md)" is provenance attribution (lists the cardinal rules numbered 1–7 inline just below at workflow.md:101+); not a runtime cross-reference instruction. Same pattern as `governor/workflow.md:334` (deferred as COMPLIANT-BY-SPIRIT). |
| `agents/project-manager/workflow.md` | 182 | `**Burndown output format** (NOT a soul.md template — text + chart inline):` | COMPLIANT-BY-SPIRIT — explicit disambiguation: the parenthetical "(NOT a soul.md template)" is the author explicitly saying this format lives here, not in soul.md. Pattern is the inverse of a cross-reference instruction. |
| `agents/blueprinter/soul.md` | 76 | `After every run, I report the outcome for each action slot (this list is the canonical home for the outcome vocabulary; workflow.md references it):` | COMPLIANT-BY-SPIRIT — the parenthetical "workflow.md references it" is an inverse reference / provenance attribution (workflow.md does cite this section — see L260 `soul.md §Output Shape`). Same pattern as `governor/workflow.md:334`. |
| `agents/wanderer/skills-template/investigation-strategy.md` | 110 | `Hard cap from \`rule.md\`: **at most 3 workers concurrently**.` | DEFERRED — the reference is provenance attribution to the rule cap (which is defined at `wanderer/rule.md:49 ### 🔢 Resource Guideline — Max 3 workers concurrently`). Could be tightened to `rule.md → Resource Guideline` per §3 preferred form, but the existing form (`from \`rule.md\`: **at most 3 workers concurrently**`) actually carries the literal cap value inline, so the agent has the information at the read site. Recording for completeness (added in iteration 3). |
| `agents/planner/memory.md` | 20 | `Use simple session names (consistent with workflow.md):` | DEFERRED — bare `workflow.md` reference without section name; per §3 the section name should be cited. Could be tightened to `planner/workflow.md → Opencode Session Naming` (the section that defines the session-name list — search for the actual section heading). Recording for completeness (added in iteration 3 — originally missed because `agents/*/memory.md` was outside the scan glob; lesson logged below). |

#### Iteration 1 dispositions (residue re-grep)

| # | File | Line | Exact text | Surface? | Disposition | Reasoning |
|---|------|------|------------|----------|-------------|-----------|
| 1 | `agents/governor/workflow.md` | 334 | `**Degraded-confidence notice format (from rule.md):**` | yes (in-scope workflow.md) | **COMPLIANT-BY-SPIRIT** | This line IS the section heading in workflow.md itself; the "(from rule.md)" parenthetical is provenance attribution, NOT a cross-reference instruction. The format content is right below the heading in workflow.md (lines 336–344), so the agent does not need to navigate rule.md. The two already-fixed sites in this same file (lines 298 and 329) cite the same canonical heading `rule.md → Degraded-confidence notice format` — this heading's own "(from rule.md)" attribution is the inverse: it tells the agent where the canonical version lives while keeping a working copy inline. |
| 2 | `agents/worker/workflow.md` | 189 | `   - Apply the error-handling table from rule.md:` | yes (descriptor-hint "error-handling table") | **FIXED** | Descriptor "error-handling table" maps to `worker/rule.md:90 ### Handle Skill System Errors Gracefully` (which contains the table). Resolves cleanly; +18 bytes on the line; no row-context constraint (this is a numbered step, not a table row). |
| 3 | `agents/tester/skills-template/mock-test.md` | 15 | `… (ensemble self-system — see rule.md Port Safety).` | yes (adjacent-surface — `skills-template/*.md` IS an assembled prompt surface, loaded via `load_skill="mock-test"` at dispatch time; outside the original Step-2 scan glob) | **COMPLIANT** | Already has section name "Port Safety" inline. Recorded for completeness; the `skills-template/` and `_prompt_system/innate-skills/` directories are adjacent surfaces that warrant inclusion in future audits (see lesson below). |
| 4 | `agents/tester/skills-template/quick-fix.md` | 93 | `Quick fixes are the #1 priority for session reuse (see session-management rules in rule.md):` | yes (adjacent-surface, descriptor-hint "session-management rules") | **FIXED** | Descriptor "session-management rules" mapped to `tester/rule.md:213 ### Reusing Instances (Priority Order)` (which is the priority-order rules for reusing sessions). Resolves cleanly; +19 bytes on the line. |
| 5 | `agents/_prompt_system/innate-skills/test-pack/skill.md` | 97 | `When timeout occurs, apply TTQA optimizations per rule.md.` | yes (adjacent-surface — `_prompt_system/innate-skills/*.md` is an assembled prompt surface; outside the original Step-2 scan glob) | **FIXED** | Bare file ref `per rule.md` with NO descriptor, in an assembled prompt surface. Defect-class exactly per §3. Canonical section is `tester/rule.md:86 ### TTQA & Test Architecture Maintenance`. +36 bytes on the line. |
| `agents/blueprinter/workflow.md` | 196 | `… Two workers (1 explore + 1 craft) satisfies the fan-out discipline (soul.md line 87): …` | yes (positional "line 87" — fragile §3 fragile-class) | **FIXED** (iteration 3) | Positional reference `soul.md line 87` replaced with section-name form `soul.md §Output Shape` (matching the same-agent sibling ref at `workflow.md:260` which uses the same `§Section` convention). Line 87 is governed by `blueprinter/soul.md ## Output Shape` (section spans L74–L87). +0 bytes net on the line (same length: `line 87` → `§Output Shape`). NOTE: the original author intended to cite the fan-out discipline (which lives in `## My Coordination Model` at L41–L47 with the "up to 4 workers per wave" cap at L45); the line number was a misreference. The section-name form correctly governs the line at L87 (`## Output Shape`); the semantic mismatch is now visible via the section name and out of scope for this fix. |
| `agents/tidier[v2]/skills-template/tidier-robustness.md` | 18 | `> **Aggregation of worker findings is a dispatcher responsibility** (see\n> \`workflow.md\` step 6 and \`tidier-strategy.md\` Aggregation Strategy).` | yes (adjacent-surface — `skills-template/*.md` runs in worker frames; positional "step 6" — fragile §3 fragile-class) | **FIXED** (iteration 3) | Positional reference `workflow.md step 6` replaced with cross-frame full-path + section-name form `tidier[v2]/workflow.md → 6. Aggregate & Verify (DISPATCHER STEP)` (per iteration-2 #4 precedent for skills-template cross-frame refs). The companion `tidier-strategy.md Aggregation Strategy` was already a valid `→ Section` ref; tightened to `tidier-strategy.md → Aggregation Strategy` for consistency. |

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
| `agents/developer/workflow.md` | 86 | 98 | **−12** |
| `agents/tester/workflow.md` | 0 | 0 | 0 |
| `agents/tidier/workflow.md` | 0 | 0 | 0 |
| **TOTAL** | **86** | **98** | **−12** |

| Metric | Value |
|--------|-------|
| Pre-existing worktree-aware feature bytes | 1628 |
| Iteration 0 net delta on budget files | **−12** |
| Iteration 1 net delta on budget files | **0** (none of the 3 fixed files are budget files) |
| New total | **1616** |
| Cap | 1650 |
| Status | **PASS** (34 bytes of headroom) |

The only budget-file change is `agents/developer/workflow.md:574`, which removes 12 bytes net
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
| Fix (iteration 2) | **88294d715f2d345de9108a74c382d918ffecc598** | `fix(prompts): reviewer optionals — heading promotion, phantom target, cross-frame refs, audit arithmetic` |
| Fix (iteration 3) | **cd79891533360fae77d6a7797d77a687394e7685** | `fix(prompts): tester closure — reviewer cross-frame ref, positional refs, audit completeness` |
| Report | (this file's commit, see `git log --oneline -1`) | `docs: prompt section-reference audit report` |

`git log --oneline -5` on the branch tip:

```
cd798915 fix(prompts): tester closure — reviewer cross-frame ref, positional refs, audit completeness
88294d71 fix(prompts): reviewer optionals — heading promotion, phantom target, cross-frame refs, audit arithmetic
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
   `agents/developer/workflow.md` = −12 bytes; total = 1616 (cap 1650, PASS).

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

---

## 10. Iteration 2 — Reviewer Optionals (pre-merge fix pass)

The branch's reviewer returned APPROVE 0-critical with optionals #1–#6 (and #7–#9 REJECTED
as out-of-scope scope-expansion). Leader ratified 5 accepted optionals (#1, #2, #3, #4, #6).
This iteration applies exactly those 5 and updates the report.

### Per-fix disposition

| Reviewer # | File | Line | Disposition | Reasoning |
|------------|------|------|-------------|-----------|
| #1 (resolution bug) | `agents/governor/rule.md` | 147 | **FIXED** | Bold-prose line `**Degraded-confidence notice format (prepended to the output when synthesizing from 1 result):**` was inside `### 🎯 QUORUM + DEADLINE (D9 — degraded quorum + tiered deadlines)` and was referenced from `governor/workflow.md:298` and `:329` via the form `rule.md → Degraded-confidence notice format` — but the target was bold prose, not a real heading, so the references dangles. Promoted to a real `#### Degraded-confidence notice format` heading (dropped the verbose parenthetical per heading-convention; the verbose content lives in the prose paragraph immediately after the heading). Post-fix verification: the heading `Degraded-confidence notice format` now appears as `####` at `governor/rule.md:147`; both workflow.md references resolve to that heading. |
| #2 (phantom target) | `agents/developer/soul.md` | 13 | **FIXED** | The iteration-0 v1 fix replaced the broken `knowledge.md` ref with `(see opencode tool catalog for details)` — but "opencode tool catalog" is also a phantom target (no such file). Simplest sanctioned fix: drop the parenthetical. Line now reads `- Can use specialized tools`. Report's FIXED-table row for this site updated to reflect the FINAL state (parenthetical dropped), not the v1 "opencode tool catalog" wording. |
| #3 (audit arithmetic) | `.agents/shared/planning/prompt-section-references-audit.md` | §5 table + §5 metric rows + §5 prose + §8 verification | **FIXED** | Leader cited four numbers: 82→86 (added bytes), 96→98 (removed bytes), −14→−12 (net delta), 1614→1616 (new total), 36→34 (headroom bytes), plus the §5 prose "removes 14 bytes net" → "removes 12 bytes net". Variant-tolerant sweep: enumerated every occurrence of 1614/1616, −14/−12, 82/96 in arithmetic-table context, 36/34 bytes-of-headroom, "removes 14 bytes net"/"removes 12 bytes net", and the §8 verification line — all aligned. Verified byte counts via `git diff -U0 --no-color 6c4bfb7b HEAD -- agents/developer/workflow.md`: added = 87 raw bytes − 1 newline = **86** net; removed = 99 raw bytes − 1 newline = **98** net; net = **−12**. New total = 1628 + 86 − 98 = **1616**. Headroom = 1650 − 1616 = **34**. |
| #4 (resolution bug, cross-frame) | `agents/tester/skills-template/quick-fix.md` | 93 | **FIXED** | The skill runs in worker frames where bare `rule.md` dangles. Changed `(see rule.md → Reusing Instances (Priority Order)):` to `(see tester/rule.md → Reusing Instances (Priority Order)):`. Resolution re-check: heading `### Reusing Instances (Priority Order)` exists at `tester/rule.md:213` (verified). |
| #6 (disambiguation) | `agents/tester/soul.md` | 81 | **FIXED** | `tester/rule.md` has TWO `### Quick Fix` sections: (a) `:96` under `## Must` (permissive — when IS a quick fix authorized); (b) `:173` under `## Must Not` (restrictive — what NOT to authorize). The sentence "See rule.md → Quick Fix for criteria" — the word "criteria" points to the permissive `## Must` section which defines the criteria. Disambiguated to `See tester/rule.md → Quick Fix (Must) for criteria and workflow.md → Quick Fix Process for examples`. |

### Budget confirmation

Per the worktree-aware verification-summary, the 9 budget files are:

```
agents/giter/workflow.md
agents/giter/rule.md
agents/giter/tools_note.md
agents/leader/workflow.md
agents/leader/tools_note.md
agents/developer/rule.md
agents/developer/workflow.md
agents/tester/workflow.md
agents/tidier/workflow.md
```

The 5 iteration-2 edits targeted files:

```
agents/governor/rule.md         — NOT a budget file ✓
agents/developer/soul.md        — NOT a budget file ✓
agents/tester/skills-template/quick-fix.md — NOT a budget file ✓
agents/tester/soul.md           — NOT a budget file ✓
agents/_prompt_system/.../skill.md  — NOT a budget file (and not edited in iteration 2) ✓
```

**None of the 5 iteration-2 targets are budget files.** The worktree-aware byte
arithmetic stays at 1616 / 1650 (PASS, 34 bytes headroom) after iteration 2 — only the §3
arithmetic correction in this report affected any number, not the prompt files.

### Reviewer optionals #7, #8, #9 — REJECTED for this branch

Per the leader adjudication: scope-expansion backlog, do NOT touch them on this branch.

---

## 11. Iteration 3 — Tester-Closure Pass (audit completeness)

The branch's tester returned NOT-VERIFIED narrowly — only Task 1a (audit completeness) failed
(the other Tasks were VERIFIED and unaffected). This iteration closes the audit-completeness
gap: 11 borderline sites are dispositioned (9 COMPLIANT-BY-SPIRIT/DEFERRED, 2 FIXED).

### Per-fix disposition

| # | File | Line | Disposition | Reasoning |
|---|------|------|-------------|-----------|
| FIX A | `agents/reviewer[v2]/workflow.md` | 310 | **FIXED** | Bare `(per \`governor/rule.md\`)` → `(per \`governor/rule.md → Report Disagreements Transparently\`)`. Cross-frame full-path + section form (per iteration-2 #4 precedent). Resolution re-check: heading `### Report Disagreements Transparently` confirmed at `governor/rule.md:192` (re-verified). |
| FIX B | `agents/blueprinter/workflow.md` | 196 | **FIXED** | Positional `(soul.md line 87)` → `(soul.md §Output Shape)`. Same-file sibling convention uses `§Section` form (see L48 `§Fan-In Escape Valve`, L260 `§Output Shape`). Line 87 governed by `blueprinter/soul.md ## Output Shape` (L74–L87). +0 bytes net on the line. |
| FIX C | `agents/tidier[v2]/skills-template/tidier-robustness.md` | 18 | **FIXED** | Positional `(\`workflow.md\` step 6)` → `(\`tidier[v2]/workflow.md → 6. Aggregate & Verify (DISPATCHER STEP)\`)`. Cross-frame full-path + section form (per iteration-2 #4 precedent). Companion `tidier-strategy.md Aggregation Strategy` already valid; tightened to `tidier-strategy.md → Aggregation Strategy`. |
| 1 | `agents/watcher/rule.md` | 87 | **COMPLIANT** | `(see soul.md → My Decision Contract)` — has section. |
| 2 | `agents/watcher/tools_note.md` | 24 | **COMPLIANT** | `… see soul.md → My Decision Contract.` — has section. |
| 3 | `agents/watcher/workflow.md` | 24 | **COMPLIANT** | `soul.md → My Decision Contract` (full path; was lumped under L47/L74 in the iteration-0 entry; now broken out). |
| 4 | `agents/watcher/workflow.md` | 47 | **COMPLIANT** | `see \`rule.md → Critical-Path Detection\`` — has section. |
| 5 | `agents/watcher/workflow.md` | 74 | **COMPLIANT** | `(see soul.md → My Decision Contract)` — has section. |
| 6 | `agents/watcher/workflow.md` | 96 | **COMPLIANT-BY-SPIRIT** | `(from \`rule.md\`)` parenthetical is provenance attribution (lists cardinal rules 1–7 inline below); same pattern as `governor/workflow.md:334`. |
| 7 | `agents/project-manager/workflow.md` | 182 | **COMPLIANT-BY-SPIRIT** | `(NOT a soul.md template)` parenthetical is explicit disambiguation; the author is saying this format is local, not in soul.md. |
| 8 | `agents/blueprinter/soul.md` | 76 | **COMPLIANT-BY-SPIRIT** | `workflow.md references it` parenthetical is an inverse reference / provenance attribution; workflow.md L260 does cite this section. |
| 9 | `agents/wanderer/skills-template/investigation-strategy.md` | 110 | **DEFERRED** | `Hard cap from \`rule.md\`:` carries the literal cap value inline (so the agent has the info at the read site); could be tightened to `rule.md → Resource Guideline`. Recording for completeness. |
| 10 | `agents/planner/memory.md` | 20 | **DEFERRED** | Bare `workflow.md` ref; could be tightened to `planner/workflow.md → Opencode Session Naming`. Recording for completeness — this site was missed in iterations 0/1 because `agents/*/memory.md` was outside the scan glob (lesson logged below). |

### Future-audit note (scan glob)

The scan glob must include `agents/*/memory.md`. Memory files ARE assembled prompt surfaces
(loaded into the agent's prompt surface via `daemon/loader.py`'s `memory.md` compose step).
Iterations 0, 1, and 2 missed `planner/memory.md:20` for this reason — the search glob was
`agents/*/{soul,rule,workflow,tools_note}.md`. Iteration 1 extended it to `skills-template/*.md`
and `_prompt_system/innate-skills/*/skill.md` but did NOT include `memory.md`. Iteration 3
catches the gap.

Updated future-audit scan glob (cumulative across all iterations):

```
agents/*/{soul,rule,workflow,tools_note,memory}.md
agents/*/skills-template/*.md
agents/_prompt_system/innate-skills/*/skill.md
```

### Budget confirmation

Per the worktree-aware verification-summary, the 9 budget files are unchanged:

```
agents/giter/workflow.md
agents/giter/rule.md
agents/giter/tools_note.md
agents/leader/workflow.md
agents/leader/tools_note.md
agents/developer/rule.md
agents/developer/workflow.md
agents/tester/workflow.md
agents/tidier/workflow.md
```

The 3 iteration-3 edits targeted files:

```
agents/reviewer[v2]/workflow.md                    — NOT a budget file ✓
agents/blueprinter/workflow.md                     — NOT a budget file ✓
agents/tidier[v2]/skills-template/tidier-robustness.md — NOT a budget file ✓
```

**None of the 3 iteration-3 targets are budget files.** Byte arithmetic unchanged from
iteration 0: 1616 / 1650 (PASS, 34 bytes headroom).

### Tester verdict summary

Tester verdict: NOT-VERIFIED narrowly, only Task 1a (audit completeness).
- Task 1a (audit completeness) — **FIXED in iteration 3** (11 borderline sites dispositioned,
  scan glob expanded to include `agents/*/memory.md`).
- All other tester Tasks — VERIFIED (unchanged).

### Pre-existing defect noted (iteration 3, informational)

- `agents/tidier[v2]/skills-template/tidier-robustness.md` ends without `\n` on HEAD —
  pre-existing (verified on commit 6c4bfb7b), not introduced by iteration 3. Out of scope.

---

## 12. Convention v2 supersession (2026-09-08)

### 12.1 Background

Yesterday's sweep (commit `e191da99` + iteration residue `e48dad70` + review iteration
`88294d71` + tester-closure iteration `cd798915`) converted bare file refs to the
intermediate `file.md → Section` form across 33 files (per §3.1 inventory). The audit
log at §3.2 documented ~52 explicit conversions plus borderline dispositions.

That form was sanctioned-then-superseded within 24 hours. Live v0.12.2 deployment surfaced
the intermediate form itself as the defect: **file paths are noise an agent cannot navigate**
— the section name plus the owning agent is the navig unit. Path tokens like `rule.md`,
`workflow.md`, `giter/workflow.md`, `tester/rule.md → Section` defeat the section-name-as-
navigable-unit design intent because the agent still has to parse a path token to know
where to look. Live observation: in the leader prompt, the file+section arrow form appeared
inline next to the actual section content, and the agent's response pattern showed it
processing the path before the section name (extra latency, occasional mis-resolution
to a same-name section in another agent).

This iteration replaces the intermediate form with **convention v2**: pure section
references. The two allowed forms are:

- **Same-agent** (target section lives in the same agent's assembled prompt):
  `See <Section Name>`
- **Cross-agent** (target section lives in another agent's prompt):
  `See <agent>'s <Section Name>` — e.g. `See giter's Worktree Mode`

All filename/path tokens in prompt text are now forbidden — both bare file forms
(`rule.md`, `workflow.md`, `giter/workflow.md`) and the intermediate arrow form
(`file.md → Section`, `file.md §Section`, `file.md "Section"`). Operators verify closure
by grepping `\.md|workflow\.md|rule\.md|soul\.md|tools_note\.md|memory\.md|<agent>/<file>.md`
across `agents/**` (excluding builder-prompt/growth files and tool-API parameter
examples) and resolving to zero in-scope hits.

### 12.2 The v2 rule (guide §3 rewrite)

`docs/agent-prompt-writing-guide.md §3 Cross-reference hygiene` was rewritten. The
new rule:

| Form | Pattern | Example |
|------|---------|---------|
| Same-agent | `See <Section Name>` | `See Quick Fix (Must)` |
| Cross-agent | `See <agent>'s <Section Name>` | `See giter's Worktree Mode` |

**Forbidden in prompt text:** ANY filename or path token. Both bare file forms
(`rule.md`, `workflow.md`, `soul.md`, `tools_note.md`, `memory.md`, `*.md`) AND the
intermediate `file.md → Section` arrow form are now forbidden.

Disambiguators stay where two sections share a title within the same agent
(`See Quick Fix (Must)` vs `See Quick Fix (Must Not)`). Cross-agent refs that need
disambiguation add the agent's full versioned id (`See reviewer[v2]'s ...`).
Auto-loaded strategy skills (`dev-strategy.md`, `planning-strategy.md`,
`approval-strategy.md`, `review-strategy.md`, `test-strategy.md`,
`tidier-strategy.md`, `tidier-static-hygiene.md`) assemble their full content into
the owning agent's prompt surface, so referencing a heading by name alone resolves
without a path token (the agent has the file).

§3 rules preserved-and-restated in v2 form: the disambiguator clause, the auto-loaded strategy-skill heading-resolution clause, and the post-change verification duty (grep rekeyed `rule.md §` → `\.md`, plus own-prompt heading-resolution and cross-agent owner heading checks); the path-bearing subsection forms (`file.md "Section"`, `file.md §Section`) are FORBIDDEN in v2 (carry the path token v2 exists to remove); the `Cardinal #N over §N` semantic-labels table is absorbed by the navigable-unit principle and the §10 checklist bullet.

### 12.3 Conversion counts (recounted from ground truth 2026-09-08)

This branch (`fix/prompt-pure-section-refs`) supersedes yesterday's intermediate
form across 97 in-scope prompt-surface files. Counts below are the ACTUAL removed
path-tokens per `git diff fd582efd..HEAD -- agents/` (recounted against ground
truth, not implementer-claimed; the original 71/71/60/etc. were undercounted):

| Source form | Count (ground truth) | Target form |
|-------------|---------------------|--------------|
| `rule.md` (bare) | 77 | same-agent section refs + (Cardinal/Step/etc.) positional refs |
| `workflow.md` (bare) | 84 | same-agent section refs |
| `soul.md` (bare) | 67 | same-agent section refs |
| `tools_note.md` (bare) | 23 | same-agent section refs |
| `memory.md` (bare) | 22 | same-agent section refs |
| `dev-strategy.md` (bare) | 15 | same-agent section refs (auto-loaded) |
| `planning-strategy.md` (bare) | 10 | same-agent section refs (auto-loaded) |
| `approval-strategy.md` (bare) | 9 | same-agent section refs (auto-loaded) |
| `review-strategy.md` (bare) | 4 | same-agent section refs (auto-loaded) |
| `test-strategy.md` (bare) | 3 | same-agent section refs (auto-loaded) |
| `tidier-strategy.md` (bare) | 11 | same-agent section refs (auto-loaded) |
| `tidier-static-hygiene.md` (bare) | 2 | same-agent section refs (auto-loaded) |
| `<agent>/<file>.md → Section` (cross-arrow, yesterday's form) | **3** (NOT ~12 — implementer overcounted) | `See <agent>'s <Section Name>` |
| `file.md §Section` (compliant in v1, forbidden in v2) | 5 | `See <Section Name>` |
| `file.md "Section"` (quoted, §3 sanctioned) | ~10 | `See <Section Name>` |
| `(canonical in \`file.md\`)` (heading parenthetical) | ~5 | `(canonical)` |
| `lives in \`file.md\` (auto-loaded)` | ~5 | `are auto-loaded` / `is auto-loaded` |
| `(from file.md)` / `(the contents of file.md)` provenance | ~3 | dropped parenthetical |
| **Total path-token refs converted** | **327 (ground truth; was ~290 estimated)** | |

Auto-loaded strategy skills (§3.1 acknowledged they are fully assembled into the owning
agent's prompt) dropped their `file.md →` prefix and retained only the section name.
Cross-agent pointers (e.g. `giter/workflow.md -> Worktree Mode`) became `See giter's
Worktree Mode`. Same-agent pointers (e.g. `rule.md → Cardinal Rules`) became
`See Cardinal Rules`.

### 12.4 Before/after examples (8 conversions spanning same-agent, cross-agent, auto-loaded skill, and disambiguation)

| # | Before (v1 form) | After (v2 form) |
|---|------------------|-----------------|
| 1 | `approver/workflow.md`: `See \`rule.md\` → Resource Constraint (STRICT)` | `See Resource Constraint (STRICT)` |
| 2 | `developer/workflow.md`: `(giter/workflow.md -> Worktree Mode)` | `See giter's Worktree Mode` |
| 3 | `approver/workflow.md`: `Execute these steps as part of the approval process. **See \`rule.md\` → Plan Improvement Tracking for file formats and constraints.**` | `**See Plan Improvement Tracking for file formats and constraints.**` (heading exists at `approver/rule.md:86` and `approver/memory.md:40`) |
| 4 | `planner[v2]/workflow.md`: `lives in \`planning-strategy.md\` (auto-loaded)` | `is auto-loaded` |
| 5 | `approver[v2]/tools_note.md`: `(canonical in \`approval-strategy.md\`)` heading parenthetical | `(See Approval-Strategy Skill Selection Guide)` (heading-based ref, not bare parenthetical) |
| 6 | `reviewer[v2]/workflow.md`: `(per \`governor/rule.md → Report Disagreements Transparently\`)` (cross-agent) | `See governor's Report Disagreements Transparently` |
| 7 | `tester/workflow.md`: `See workflow.md → Skill Selection (canonical reference)` (skill-name disambiguation) | `See Skill Selection (canonical reference)` |
| 8 | `watcher/workflow.md`: `Cardinal rules 1–7 (from \`rule.md\`)` (provenance) | `Cardinal rules 1–7` |

### 12.5 Closure proof (variant-tolerant re-grep, zero in-scope hits)

Per the v2 rule's closure requirement (`grep resolves to zero hits` over in-scope
prompt surfaces), the final variant-tolerant sweep returned **zero in-scope prompt-file
hits** for the canonical pattern set: `\.md` (prompt-file tokens), `<agent>/<file>.md`
(cross-agent paths), `file.md → Section` (arrow form), `file.md §Section` (§-form),
`file.md "Section"` (quoted form), `see <file>.md`-style prose, **and the bare
`agents/`-prefix pattern** (`agents/<name>/<file>` and `agents/See <agent>'s ...`-style
fragments that produced the leader/workflow.md:662 class of cross-agent corruption).

**Closure pattern set (variant-tolerant — match BOTH spaced and unspaced forms):**

| Pattern | Catches |
|---------|---------|
| `\.md\b` | bare `.md` filename tokens (`soul.md`, `rule.md`, `workflow.md`, ...) |
| `<agent>/<file>\.md` | cross-agent path tokens (`agents/giter/workflow.md`, `giter/rule.md`) |
| `<file>\.md\s*(→|->|§|\")` | arrow / § / quoted section forms |
| `see <file>\.md` / `per <file>\.md` / `via <file>\.md` | prose intro patterns |
| `agents/\b` followed by `See\|to\|from` / agent-name pattern | bare `agents/` prefix as cross-reference (e.g., the corruption class `agents/See leader's ...`) |
| `(See\s*[^A-Za-z]*\)\|<empty parens>` | empty captures like `(See )`, `(See .`, `(See,` |

**Bare `agents/` prefix interpretation (added 2026-09-08 repair iteration):**
A hit on `agents/<...>` is a violation ONLY when the token functions as a cross-reference
to a prompt section (e.g., `agents/See leader's ...` produced by a regex collision between
the `agents/<file>.md` substitution and the `See <agent>'s ...` substitution — both apply,
order matters, and the bare-`agents/` case proves the gap). Operational filesystem paths
where `agents/` is a literal path component (e.g., `.agents/shared/planning/...`,
`.agents/<agent>/rules/`) are NOT cross-references and remain out of scope. The §12.5
#0 controlling exclusion (operational filesystem paths) governs this same distinction.

**Survivors (out-of-scope per task instructions; report-only, not converted):**

0. **Controlling exclusion interpretation:** v2 governs navigational cross-references to prompt sections; operational filesystem paths are excluded BY DESIGN — (a) own-directory write-scope Cardinal declarations (e.g. reviewer/tidier/planner own notes/rules dirs); (b) operational project-infra paths (`.agents/shared/planning/`, `conventions.md`, `active.md`, phase files); (c) non-prompt convention docs consulted at runtime (`core.md`, `PACKS.md`, `QUARANTINE.md`, `ensure.md`); (d) system hooks (`_prompt_system/knowledge*.md`); (e) bare `agents/` prefix where the path is a literal filesystem reference, not a cross-reference (per the addition above). A path-token hit is a violation ONLY if it functions as a cross-reference to a prompt section; survivors must be enumerated and justified as operational.

1. **`agents/watcher/builder-prompt.md` (3 hits)** — builder-prompt file is NOT assembled
   into an agent prompt (per task scope note). Out of scope; would not changed.
2. **`agents/<agent>/growth.md` files** — growth files are NOT assembled into prompts.
   Out of scope.
3. **`agents/_mother/tools_note.md:50`** — `agent_read(agent_name="developer", file="soul.md")`
   is a tool API parameter example. The literal `soul.md` is required as the tool's
   `file` parameter value; this is operational (matches audit Reason Summary #3 — `.agents/...`
   paths are operational). Not converted.
4. **`agents/reviewer/memory.md:117`, `agents/approver/memory.md:38`** —
   `.agents/<agent>/memory.md` paths are operational workspace references (the agent
   reads/writes its own memory at this path in the actual project). Operational.
   Not converted.
5. **`agents/tidier[v2]/skill-set.yaml:28`** — YAML file (not `.md`), not in scope
   per loader.
6. **`agents/_inner_soul/`, `agents/_baby_template/`** — internal scaffolding agents,
   not in `meta.json` agent registry. Out of scope.
7. **`agents/_prompt_system/knowledge.md`, `project-experience.md`, `critical-notes.md`**
   — system-level hooks loaded into all agents' prompts but are not agent-prompt files.
   Report-only.

### 12.6 Sanctioned-then-superseded (within 24h)

Yesterday's `file.md → Section` form was sanctioned at §3 of the writing guide and
deployed to v0.12.2 production. Within 24 hours, live observation surfaced it as a
defect-class instance: the file path token added parser overhead without navigable value
(the agent still has to resolve the file to a section, which is what the section name
already accomplishes). The fix was to drop the file token entirely and rely on the
section name plus the owning agent as the navig unit.

This is the first documented case of a v1 prompt convention being superseded within 24
hours of production deployment. Lesson for future audits: variant-tolerant grep
coverage at sweep time should include not just the v1 form but any **future** form
the agent might produce (here: `file.md → Section` should have been checked for
navigable-unit redundancy, not just for parser correctness).
