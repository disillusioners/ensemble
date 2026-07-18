# Lesson: workflow.md missing `skill_feedback` tool-name (W2 terminology sync gap)

**Date:** 2026-07-18
**Test:** Test 4 — Cross-File Terminology Sync
**Severity:** Low-medium (documentation consistency, not functional)
**Status:** UNFIXED (pending author action)

## Symptom
`skill_feedback` tool name appears in rule.md (Dispatch Model glossary, line 5), soul.md (line 64), and tools_note.md (lines 27, 33), but is ABSENT from workflow.md. The workflow.md "Skill-Per-Worker Dispatch Pattern" section (line 45) discusses "1:1 attribution" conceptually (lines 99, 1079) but never names the tool that implements it.

## Root Cause
The Phase 2 rewrite introduced a glossary preamble in rule.md (line 3, "## Dispatch Model (Glossary)") to centralize dispatch terms. rule.md, soul.md, and tools_note.md were updated to reference `skill_feedback` through the glossary. workflow.md — a much larger file (51KB, 1000+ lines) — had its Dispatch Pattern section touched for the conceptual framing but the canonical tool name `skill_feedback` was not threaded into that section's prose.

This is a classic large-file sync miss: the glossary technique works for files that explicitly cite it, but workflow.md's dispatch section uses its own parallel prose.

## Fix (quick-fix eligible, ~20 words)
Add one sentence to workflow.md near line 99 (or line 1079, the mirrored decision-point) in the Skill-Per-Worker Dispatch Pattern section:

> "Worker calls `skill_feedback(skill_id, applied=True/False)` after each task for clean 1:1 attribution (see Dispatch Model glossary in rule.md)."

## Prevention
When centralizing terms via a glossary preamble, grep ALL files (incl. large ones like workflow.md) for the conceptual phrase ("1:1 attribution") and ensure the canonical tool name is added at each mention. A grep checklist for the 4 dispatch terms (`worker`, `load_skill`, `skill_feedback`, opencode-as-fallback) across all 5 tester prompt files (soul/rule/tools_note/workflow/meta) would catch this class of miss.

## Verification After Fix
Re-run Test 4 from the validation script:
`grep -n "skill_feedback" agents/tester/workflow.md` → expect ≥1 hit.
