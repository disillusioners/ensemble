# 2026-07-27 — Council Skill Passthrough Review (convene_council_with_skill)

## Target
`feature/council-skill-passthrough` @ `efc652bc` — new `convene_council_with_skill` tool threading `councilor_skill` through the council system. 8 files, +217/-47.

## Outcome: 🔴 BLOCKING (1 critical, 4 warnings, 5 suggestions)
Council found a ship-blocker that the pre-council surface scan missed.

## Key Findings
- 🔴 **F1 (BLOCKER):** Tool defined at `instance.py:960` but NEVER added to the `tools` list at `:1378`. Feature is dead code — produces "tool not found" at runtime. Reviewer[v2] callsites (`rule.md`, etc.) all point at a non-existent tool.
- 🟡 **F3:** `councilor_skill` f-string-interpolated into governor message with no newline sanitization → LLM-directive injection surface. Fix: strip `\n`/`\r` at validation gate.
- 🟡 **F4:** Zero test coverage for new tool; `TestConveneCouncilWithSkill` class missing entirely.
- 🟡 **F5:** WARN-only skill check silently degrades when `config.skill_evolution is None` (then `_skill_repo=None`).
- 🟡 **F6:** Parameter ordering breaks symmetry with `convene_council`.
- 🟢 **F7:** Drift risk — two near-identical council tools. Suggest folding `councilor_skill` into `convene_council` as optional param.
- 🟢 **F2 (DEFENSIBLE):** Default councilor `wanderer→worker` is actually justified: `worker` has `skill_injection: true` (wanderer doesn't), and `worker` lacks `instance` tool (no recursion path). But rule.md rationale text is misleading.

## Method
- Deep-Review mode triggered (Security/Injection + Cross-Cutting + Workflow).
- Single `review-deep` council session with 8-point prompt covering all focus areas.
- Council found the blocker via direct file read (tools list at :1378).
- Reviewer independently verified F1 (`grep` confirmed) and F2 (worker meta.json `skill_injection: True`).

## Lesson for Future Reviews
- **Tool-binding check is non-obvious.** When reviewing a new tool in `instance.py`, ALWAYS grep both the function definition AND the `tools = [...]` list. A defined-but-unlisted tool is silent dead code — no build failure, no test failure, just runtime "tool not found".
- The pre-council surface scan (reading the diff) could not catch F1 because the diff for `instance.py` only shows the +112 added lines (the new tool body), not the unchanged `tools` list. The council reading the full file caught it.
- The `wanderer→worker` default change looked risky on the surface (worker has bash/proc/filesystem write tools) but was structurally justified by `skill_injection: true`. Don't flag a default change without checking the flag that makes it REQUIRED.
