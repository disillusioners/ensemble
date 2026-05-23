# Phase 3: Agent Definitions — Leader Update, Experiencer Stripping, Shared Prompt Rename

## Objective
Update the leader agent's tool config, completely remove all critical notes references from the experiencer agent (including **two** CE sections in `rule.md`), and rename the shared prompt file (with `git mv`).

## Coupling
- **Depends on**: Phase 1 (Core Layer)
- **Coupling type**: loose
- **Shared files with other phases**: 
  - `agents/leader/meta.json` — only Phase 3 touches
  - `agents/experiencer/` — only Phase 3 touches
  - `agents/_prompt_system/critical-experience.md` — **`git mv`** to `critical-notes.md` in this phase
- **Why this coupling**: Agent definitions only reference tool names as strings. No Python imports from Phase 1. The coupling is "naming convention" only.

## Context
Phase 1 defined new tool names (`project_cn_add`, etc.) and new registry key (`"critical_notes"`). This phase updates agent configs to use those new names and completely strips the experiencer's CE integration.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update leader `meta.json` | Change tools.allow entry from `"critical_experience"` → `"critical_notes"`. | `agents/leader/meta.json` |
| 2 | Update leader agent docs (if any) | Check `agents/leader/soul.md`, `agents/leader/rule.md`, `agents/leader/workflow.md` for any `critical experience` or `project_ce_` references. Update terminology. | `agents/leader/` |
| 3 | Strip experiencer `meta.json` | Remove `"critical_experience"` from `tools.allow` list entirely. Do NOT add `"critical_notes"`. | `agents/experiencer/meta.json` |
| 4 | Strip experiencer `rule.md` — **TWO sections** | Remove **both** CE-related sections: **(A)** Lines 122-137: "Route High-Impact Knowledge to Critical Experience" — uses `project_ce_add()`. **(B)** Lines 199-209: "Never Route General Knowledge to Critical Experience" — mentions CE rules. ⚠️ **CAUTION**: The word "critical" at line 134 in the priority list (`critical: Security, data loss risks...`) is a **priority level word, NOT a CE reference**. Keep it. | `agents/experiencer/rule.md` |
| 5 | Strip experiencer `workflow.md` | Remove Phase 7.5 (lines 148-169) with `project_ce_add()` call format. Remove any other CE references. | `agents/experiencer/workflow.md` |
| 6 | Strip experiencer `tools_note.md` | Remove lines 304-335 (full tool documentation for CE tools). | `agents/experiencer/tools_note.md` |
| 7 | Strip experiencer `soul.md` | Update line 16 output format to remove CE reference. | `agents/experiencer/soul.md` |
| 8 | Rename shared prompt file + update content | **`git mv agents/_prompt_system/critical-experience.md agents/_prompt_system/critical-notes.md`** first. Then update all content inside: "critical experience" → "critical notes", tool names, examples. | `agents/_prompt_system/critical-experience.md` → `agents/_prompt_system/critical-notes.md` |
| 9 | Check for prompt system references | Search `agents/_prompt_system/` for any other files that reference `critical-experience` filename or content. Update imports/references to `critical-notes`. | `agents/_prompt_system/` |

## Key Files
- `agents/leader/meta.json` — Tool access config
- `agents/experiencer/meta.json` — Tool access removal
- `agents/experiencer/rule.md` — **Two** routing rules sections removal (lines 122-137 AND 199-209)
- `agents/experiencer/workflow.md` — Phase 7.5 removal
- `agents/experiencer/tools_note.md` — Tool docs removal
- `agents/experiencer/soul.md` — Output format cleanup
- `agents/_prompt_system/critical-experience.md` — **`git mv`** and rewrite

## Experiencer `rule.md` Detail

Two distinct CE sections to remove:

| Section | Lines | Title | Content |
|---------|-------|-------|---------|
| A | 122-137 | "Route High-Impact Knowledge to Critical Experience" | Uses `project_ce_add()`, routing rules for when to add CE entries |
| B | 199-209 | "Never Route General Knowledge to Critical Experience" | Rules about what NOT to route to CE |

**⚠️ Preserve**: Line 134 priority word `"critical: Security, data loss risks..."` — this is a generic priority adjective, not a CE system reference.

## Constraints
- **Experiencer must have ZERO references** to critical notes tools after this phase. No tool access, no routing rules, no workflow steps, no documentation.
- Leader keeps full access — just with new tool names.
- The shared prompt file is presumably referenced by filename somewhere (maybe a listing or index). Find and update that reference.
- Do NOT delete the experiencer agent — just strip its CE/CN integration.
- **This phase owns the `git mv` of the shared prompt file** — no other phase should rename it.

## Deliverables
- [ ] Leader `meta.json` has `"critical_notes"` in tools.allow
- [ ] Experiencer `meta.json` has no CE/CN tool references
- [ ] Experiencer `rule.md` has both CE sections removed (lines 122-137 AND 199-209)
- [ ] Experiencer `rule.md` preserves priority word "critical" at line 134
- [ ] Experiencer `workflow.md` has no Phase 7.5 / CE steps
- [ ] Experiencer `tools_note.md` has no CE tool documentation
- [ ] Experiencer `soul.md` has no CE output format references
- [ ] Shared prompt file `git mv`'d and content updated
- [ ] `grep -ri "critical.experience\|project_ce_" agents/experiencer/` returns zero results
