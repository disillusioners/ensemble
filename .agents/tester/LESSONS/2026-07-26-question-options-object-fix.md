# Lesson: `[object Object]` question-wizard bug fix verification
Date: 2026-07-26
Branch: `feature/question-options-object-fix`

## Context
LLMs occasionally return `{"text": "Option A"}` objects in a question's `options` field instead of plain strings. The frontend rendered these as `[object Object]`.

## Fix shape (verified)
- **Backend**: `_normalize_option` / `_normalize_options` in `daemon/services/question_manager.py`, applied at the QuestionPack construction site (`set_question_pack`). Coerces `{text}` dicts, bare scalars, non-list inputs, and null/empty values to a clean `list[str]`.
- **Frontend**: `optionText(opt: unknown): string` defensive helper in `question-wizard.component.ts`, wired into the template as a second layer of defense.

## Verification approach
This project has **no dedicated pack script** for question tests (not in PACKS.md). Ran the affected test files as ad-hoc packs with the dual-layer timeout:
- `tests/test_question_manager.py` — 17/17 PASS in 0.76s (2-min unit cap)
- `tests/test_question_api.py` — 4/4 PASS in 0.82s
- Frontend `npx ng build` — PASS in ~6s (strictTemplates is the authoritative check; `tsc --noEmit` skips templates)

## Takeaways
1. **Ad-hoc packs work fine for small, isolated fixes.** The question tests aren't large enough to warrant a dedicated `.sh` pack in `test/packs/`. Running the single file under `timeout 120 .venv/bin/pytest <file>` satisfies the dual-layer-timeout rule.
2. **The `{text: null}` and bare-string inputs are the highest-risk shapes** — they're the ones that leak `[object Object]` or character-split garbage. Worth a spot-check beyond the existing test file.
3. **Frontend verification must use `ng build`, not `tsc --noEmit`** (known project gotcha) — only `ng build` enforces `strictTemplates`. A `private` template-called helper would only surface here.
4. **`optionText()` taking `unknown`** (not `string`) is the right defensive signature — it lets the frontend degrade gracefully even if backend normalization is bypassed.
