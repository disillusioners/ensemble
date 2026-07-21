# Lesson: Prompt-injection sanitizer `_sanitize_note_text` was entirely untested

Date: 2026-07-21
Feature: skill_feedback upgrade (`feature/skill-feedback-upgrade`, commit da5ef6ee)
Fix commit: `a30dd72f`

## Root Cause
The skill_feedback upgrade added a prompt-injection defense helper `_sanitize_note_text` 
(`daemon/services/skill_evolution_service.py:113`) that:
- flattens newlines/tabs to spaces
- collapses whitespace runs
- truncates to `_MAX_NOTE_CHARS = 300`
- guards falsy/whitespace-only input

This is the primary defense against prompt injection via `improvement_note` / 
`feedback_improvement` fields (user-controllable text that flows into LLM prompts 
in `_build_analysis_prompt` and `_generate_evolved_content`).

**However, the original 124 tests NEVER exercised `_sanitize_note_text` directly.** 
The evolution-service prompt tests only used short, clean strings 
("Should mention PACKS.md location"). A security-critical helper had zero coverage.

## Why It Was Missed
The developer's tests focused on the "happy path" of the new feature (usefulness 
scoring, trigger firing, field persistence). The sanitizer is an internal helper 
called transitively by the prompt builders — easy to overlook when testing at the 
prompt-output level with benign inputs.

The project DOES have a prompt-injection test pattern (`tests/unit/test_shared_context_prompt_injection.py`) 
for a DIFFERENT helper (shared_context), but no equivalent existed for 
`_sanitize_note_text`.

## Fix Applied (commit a30dd72f)
Created `tests/unit/test_skill_feedback_sanitizer.py` with 13 tests in 
`TestSanitizeNoteText`:
- `test_max_note_chars_is_300` — pins the constant
- `test_truncates_at_300_chars` — 500→300 chars
- `test_truncation_rstrips_trailing_whitespace` — the `[:300].rstrip()` path
- `test_flattens_newlines_to_spaces` — `\n`, `\r\n`, `\r`
- `test_flattens_tabs_to_spaces` — `\t`
- `test_collapses_multiple_whitespace` — `re.sub(r"\s+", " ")`
- `test_empty_string_returns_empty`, `test_none_returns_empty`, `test_whitespace_only_returns_empty`
- `test_unicode_preserved` — emoji/CJK not corrupted
- `test_prompt_injection_neutralized` — `"normal\n## SYSTEM OVERRIDE\nIgnore..."` → single line
- `test_markdown_special_chars_treated_as_data` — backticks/newlines flattened to inline data

## Takeaway
**When a feature adds a security/sanitization helper, ALWAYS add direct unit tests 
for that helper** — do not rely on transitive coverage from happy-path prompt tests 
that only use benign inputs. Prompt-injection defenses specifically must be tested 
with ADVERSARIAL input (embedded headers, instruction-like text, control chars), 
because that is precisely the input the defense exists to neutralize.

**Pattern to repeat:** For any `_sanitize_*` / `_escape_*` / `_validate_*` helper 
that guards user input before it enters an LLM prompt or shell command, create a 
dedicated `test_<helper>.py` with: (1) constant pins, (2) transformation cases, 
(3) boundary cases, (4) adversarial/prompt-injection cases.

## Other Gaps Filled in the Same Commit
The same coverage analysis surfaced adjacent gaps, all fixed in `a30dd72f`:
- usefulness boundary acceptance (1 and 10) — `TestSkillFeedbackUsefulnessBoundaries`
- `_eval_low_usefulness` defensive branches (usage_repo=None, exception swallow) + custom threshold/min_samples + "scored usages" wording — `TestLowUsefulnessEdgeCases`
- `_build_analysis_prompt` mixed scoring (partial NULL) + fractional avg precision + per-record NULL usefulness — `TestAnalysisPromptMixedScoring`
