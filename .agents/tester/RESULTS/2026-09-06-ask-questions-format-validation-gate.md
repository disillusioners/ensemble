# Test Report: ask_questions input-format hardening

- **Date:** 2026-09-06
- **Branch:** `feature/ask-questions-format-validation` — feature head `5e4e33b9` (tree: 2 daemon modules + tests; commits e1986922 → 07290356 → 5e4e33b9) + test-infra commit `3088f48b` (pack script only, added by this gate)
- **Worker instances:** W1 infra `73e55ac1` (no skill) · W2 broader `46f8c1a5` (`test-pack-execution`) · W3 targeted `603f0d40` (`test-pack-execution`) · W4 functional `a9e44a8a` (`integration-test`) · W5 mock audit `f6a59836` (no skill) · commit worker `02cd1aac`
- **Verdict:** ✅ **READY** — all packs green, original symptom functionally closed, mock quality bar confirmed, zero defects, zero quick fixes needed.

## Summary

| Leg | Result | Evidence |
|---|---|---|
| Targeted pack `question_validation_targeted_unit_test` | ✅ PASS **126/126** (16s @ `3088f48b`) | Core-4 subset **73/73 exactly** (48+4+17+4, matches leader expectation); 10 question-primary files, 0 failures |
| Broader pack `regression_unit_tools` | ✅ PASS **1,155P / 0F / 1S** (11.61s @ `5e4e33b9`) | Full `tests/unit/tools/` sweep — ~12× the reviewer's 97/97 bar; +43 vs 2026-09-05 green baseline = branch-lineage net-new tests, 0 failures; TestDocsDefaultDeny clean |
| Functional close-out 3a (verbatim secondary payload) | ✅ PASS | Real tool closure + real args schema + real QuestionManager persistence: `options == ["Approve — start M1", "Approve with changes", "Not yet"]` (list[str], order preserved), `option_descriptions` additive metadata correct (field name confirmed at question_manager.py:165/407), pause flag set once, real `POST /instances/{id}/answer` → 200 `status="answered"`, resolves **by label** |
| Functional close-out 3b (malformed payloads) | ✅ PASS 4/4 | `options:[42]`, missing-label, duplicate ids, whitespace-only label → deterministic `ERROR:` hint w/ first-problem field path + MAIN spec + minimal example, returned as plain string; zero side effects each: status unchanged, pack=None, SSE=0, pause calls=0 |
| Mock-quality audit | ✅ No drift | 6 mock surfaces cataloged; all match current real signatures (manager.py:3094-3134, live_event_hub.py:384-388, get_instance KeyError @ manager.py:10099); real ToolNode + real args-schema pin CONFIRMED (validation:669-759, live re-execution reproduced exact ToolMessage); FE contract pinned (validation:196-209) |
| Quick fixes applied | 0 | No failures anywhere — nothing to fix |
| Quarantined in scope | 0 | None of the 126 pack tests appears in QUARANTINE.md (1 exonerating mention adjudicated: `question_deferred ×1` passes at HEAD, no row) |

## Scope Decision

Full suite NOT warranted: change = input validation/normalization in 2 modules (`daemon/tools/question_tools.py`, `daemon/services/question_manager.py`) + 1 test file. Ran: targeted question-surface pack (126) + full unit/tools regression slice (1,155) + real-path functional verification + mock audit. Skipped: all other partition packs (no changed files in their modules). Broader scope still exceeds the reviewer's 97/97 reference (~12×) via the tools-slice sweep.

## ensure.md Validation Results (scoped)

- **Critical #1 — No regressions in changed packs:** ✅ PASS — `question_validation_targeted_unit_test` 126/126 + `regression_unit_tools` 1,155P/0F.
- **Critical #2 — concurrency_atomic_unit_test:** ⚪ SCOPED OUT — change touches input validation/normalization only; no lock, async-conversion, or DB-concurrency surface. (Scoped by blast radius per ensure.md header; not silently skipped.)
- **Critical #3 — sync DB calls on event loop:** ⚪ SCOPED OUT — same rationale (owned by #2's pack when that surface changes).
- **Critical #4 — dev.sh `--timeout-graceful-shutdown 10`:** ✅ PASS — present at `dev.sh:102`.
- **Important #1/#2, Nice-to-have:** ⚪ SCOPED OUT — deadlock-fix-specific requirements, unrelated change set.
- **Contradictions / Improvement Notices:** none — all validated requirements were already pack-mapped and timeout-capped.

## Original Symptom Close-Out (leader item 3)

The verbatim `{label, description}` payload that originally broke the FE now flows: tool accepts → normalizes to labels-as-strings → persists pack whose FE-facing `options` is `list[str]` → descriptions ride as `option_descriptions` metadata → instance pauses normally → answer correlates by label through the real answer route. Malformed input fails deterministically with a field-path hint and zero side effects (no raise/persist/pause/SSE). **Symptom CLOSED.**

## Gaps & Follow-ups (none blocking)

- 🟢 **Coverage gap (nice-to-have):** no committed ToolNode test for the VALID-payload happy path — only the schema-rejection branch is pinned at the executor boundary (validation:683-759). Repro recipe in LESSONS/2026-09-06-ask-questions-toolnode-happy-path-gap.md. Risk mitigated for this gate by W4's real-path verification.
- 🟢 **Mock-pattern note:** `test_question_api.py:60` sets `is_write_paused` by direct attribute on MagicMock; future write-paused-branch tests there should mock the property properly (W5 deliverable-5 note).
- 🟢 **Working-tree note:** `.agents/tidier/notes.md` carries an uncommitted +8-line tidier log (documents how `5e4e33b9` was produced) — tidier's own lifecycle, deliberately left untouched by this gate.

## Process notes

- W1 branch gate tripped on the dirty tidier log; adjudicated benign (agent bookkeeping, zero overlap with code/tests/packs) and proceeded with path-scoped commit — verified post-commit: only the tidier file remained dirty.
- W2's causal note attributed the +43 delta to `tests/test_question_tools_validation.py`; corrected in aggregation: that file is top-level, outside the pack's `tests/unit/tools/` path scope — delta is branch-lineage net-new tests. Verdict unaffected (0 failures).

## Documentation Updated

- [x] PACKS.md — new row `question_validation_targeted_unit_test` (126 scope, ✅ PASS) + `regression_unit_tools` Last Run/Status updated (2026-09-06, PASS 1,155P/0F/1S)
- [x] RESULTS/2026-09-06-ask-questions-format-validation-gate.md — this report
- [x] LESSONS/2026-09-06-ask-questions-toolnode-happy-path-gap.md — coverage gap + repro
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md / QUARANTINE.md — no changes needed (no mock services used; nothing quarantined)

## Code Changes Summary (this gate)

- `test/packs/question_validation_targeted_unit_test.sh` — NEW pack script (110 lines) — commit `3088f48b`
- `.agents/tester/PACKS.md`, `.agents/tester/RESULTS/…`, `.agents/tester/LESSONS/…` — tester docs — committed by `02cd1aac` (hash in session summary)

### Overall Status
- Unit/targeted: ✅ PASS (126/126; core-4 73/73)
- Broader regression: ✅ PASS (1,155P/0F/1S)
- Functional (3a/3b): ✅ PASS / ✅ PASS
- Mock quality: ✅ PASS (no drift, executor bar confirmed)
- ensure.md (scoped): ✅ PASS (Critical #1 + #4; #2/#3 scoped out with rationale)
- **Testing Complete: ✅ READY**
