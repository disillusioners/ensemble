# Test Report: Title Generation Content-Focus Fix
Date: 2026-08-08
Branch: `feature/title-gen-content-focus`
Commit: `7c337365`
Instance IDs: `0cec5d78` (stripping test), `22b6b327` (edge case analysis), `c3c4dd6e` (branch comparison), `4894701d` (instance title regression)

## Summary
- **Overall Status: ✅ READY** — no new failures introduced, regression test is meaningful
- New regression test: ✅ PASS (`test_prompt_instructs_llm_to_ignore_git_activity`)
- Related title tests: ✅ PASS (8/8 `test_instance_title.py`)
- Pre-existing failures confirmed: ✅ 8/8 identical on both branches
- Quick fixes applied: None needed
- Quarantined: 0 tests skipped

## Scope Decision
> Full suite not requested. Change touches 2 files (1 service prompt + 1 new test) in a single module (`title_generation.py`). Blast radius: small/isolated. Ran scoped packs only — touched test file, related title test files, and analytical edge case review. Concurrency/DB-call/dev.sh ensure.md requirements are out of scope (unrelated to title generation).

## Tasks Completed

### Task 1: New Regression Test (`test_title_think_stripping.py`) — ✅ PASS
- **5 passed, 0 failed** in 0.93s
- New test `test_prompt_instructs_llm_to_ignore_git_activity`: ✅ PASS
- Tests the prompt-level guard: when the completion safety-net path fires (git-heavy instance tails), the prompt explicitly instructs the LLM to ignore git/version-control activity and focus on the user's underlying goal.

### Task 2: Related Test Files + Pre-Existing Failure Confirmation — ✅ PASS
**`test_title_generation_trigger.py`**:
| Branch | Passed | Failed | Same 8 Failures? |
|--------|--------|--------|-------------------|
| `feature/title-gen-content-focus` | 21 | 8 | ✅ Pre-existing |
| `latest` | 21 | 8 | ✅ Pre-existing |

All 8 failures are `AsyncMock` mocking issues (`run_async_no_wait` called 2× instead of 1× due to `_maybe_store_initiative_message` coroutine leaking through the mock). **Identical failure set on both branches** — confirmed pre-existing, NOT introduced by this change.

**`test_instance_title.py`**: **8 passed, 0 failed** in 0.79s — no regressions.

### Task 3: Edge Case Verification — 2 PASS, 1 PARTIAL

| Scenario | Verdict | Notes |
|----------|---------|-------|
| Messages with NO git activity | ✅ PASS | Anti-git paragraph is unconditional in prompt; LLM's internal `if` clause evaluates false → behavior unchanged for clean input |
| Messages with ONLY git activity | 🟠 PARTIAL | Prompt correctly instructs ignore+seek non-git prose, but provides **no fallback** if zero non-git prose exists (e.g., pure diff with no original request) |
| Messages with mixed content | ✅ PASS | Explicitly targeted by the fix — prompt steers LLM to prioritize task content, treat git as noise |

**Architecture note:** The anti-git instruction is **unconditional** at the code level (always embedded in prompt). The conditional behavior lives inside the prompt text itself as an LLM-side `if` clause. This is a slight mismatch with the commit message which says "conditional instruction."

### Task 4: Mock Verification — ✅ GOOD
- Mock patches `ThinkingChatOpenAI` at the import site (`title_generation.py:7`) — correct interception point
- Test inspects `mock_llm.invoke.call_args[0][0]` — the actual message list passed to the LLM in production
- Verifies prompt **text content** (substrings "ignore", git keywords), not just call counts — correct for a prompt-text fix
- Would catch regression if the prompt were reverted: ✅ YES (the `"ignore"` substring assertion is the strongest guard)
- **Gap:** Minimal — test exercises the same code path as production

### Task 5: Test Meaningfulness — ✅ YES
The regression test is **meaningful and serves its stated purpose**:
- Checks the actual production prompt text
- Removing the anti-git paragraph would break the `"ignore"` assertion
- The test docstring is honest about being a structural guard (prompt-level, not LLM-response-level)

## ensure.md Validation Results

### Core (in-scope)
- ✅ **Critical: No regressions in changed packs** — all title-related test files PASS (only pre-existing unrelated failures remain). The new test file (the actual changed pack) passes 5/5.
- ℹ️ Concurrency integrity, sync DB calls, `dev.sh` timeout — **out of scope** (unrelated to title generation prompt change). Not validated.

## Issues Found (non-blocking, follow-up items)

1. **🟠 Pure-git edge case gap (Medium)** — If a message tail is 100% git output with zero non-git prose, the prompt provides no fallback instruction. Titles may degrade to vague descriptions. Recommendation: add fallback clause ("if no non-git prose exists, derive goal from commit subjects or branch names") OR add a test pinning current behavior.

2. **🟢 Commit message wording (Low)** — Commit says "conditional instruction" but code is unconditional at the prompt-construction level. The conditionality lives inside the prompt text for the LLM. Consider tightening to "LLM-side conditional."

3. **🟢 Self-referential assertion (Low)** — Test asserts `"Merge branch" in prompt`, which is true regardless of the fix (user message body is always embedded via `{message_content[:500]}`). Doesn't weaken the test (the `"ignore"` assertion does the real work) but may confuse readers.

4. **🟢 No companion test for non-git path (Low)** — Optional: add a test asserting `"ignore" in prompt` for a clean non-git message to guard the unconditional construction against future refactors.

## Code Changes Summary
- No code changes were made during this testing session (no quick fixes needed)
- All tested code is as committed on `feature/title-gen-content-focus`

## Documentation Updated
- [x] RESULTS/2026-08-08-title-gen-content-focus-test.md — this report

---

# Re-Verification Round 2: Amended Commit a1b85f89
Date: 2026-08-08
Commit: `a1b85f89` (amended)
Instance IDs: `a9575fb9` (test execution), `14cecf4b` (analysis)

## Summary
- **Overall Status: ✅ READY** — all 6 tests pass, all 4 previous issues resolved
- **6/6 tests pass** in `test_title_think_stripping.py` (was 5 → now 6, 2 new tests added)
- Prompt quality: **GOOD** — all 3 edge cases handled, anti-git clause correctly targets raw tool output
- Quick fixes applied: None needed

## Test Results

| # | Test | Status |
|---|------|--------|
| 1 | `test_title_with_think_block_strips_reasoning` | ✅ PASS |
| 2 | `test_title_think_only_response_skips_gracefully` | ✅ PASS |
| 3 | `test_title_plain_response_unchanged` | ✅ PASS |
| 4 | `test_title_with_multiple_think_blocks` | ✅ PASS |
| 5 | `test_prompt_instructs_llm_to_ignore_git_activity` | ✅ PASS |
| 6 | `test_prompt_preserves_goal_framing_for_non_git_message` (NEW) | ✅ PASS |

Runtime: 0.86s

## New Test Verification

### Test #5 (updated) — `test_prompt_instructs_llm_to_ignore_git_activity`
- **New conditional regex assertion:** `re.search(r"\bif\b.*\bignore\b|\bif\b.*\bde-?emphasis", prompt, IGNORECASE|DOTALL)` — requires git de-emphasis to be **conditional** (`if` ... `ignore`/`de-emphasize`), not blanket suppression. This directly resolves the previous self-referential assertion concern.
- **Meaningful:** ✅ YES — would catch a revert to ALL-CAPS "IGNORE ALL GIT" (no `if` → regex fails), removal of conditional framing, or dropping git keywords.

### Test #6 (NEW) — `test_prompt_preserves_goal_framing_for_non_git_message`
- Feeds a clean non-git message ("Please fix the login bug") and asserts:
  - `"underlying goal"` is in the prompt (goal-framing preserved)
  - The conditional regex passes (anti-git instruction is unconditionally embedded in the template, not stripped for non-git input)
- **Meaningful:** ✅ YES — guards against a future refactor that makes the git clause conditionally injected based on input content.

## Previous Issues Resolution

| # | Issue | Status |
|---|-------|--------|
| 🟠 | Pure-git edge case gap (no fallback) | ✅ **RESOLVED** — Fallback: "Code Merge" / "Release Prep" added |
| 🟢 | Commit message wording | ✅ **RESOLVED** — Clear, accurate description |
| 🟢 | Self-referential assertion | ✅ **RESOLVED** — Conditional regex requires `if` clause |
| 🟢 | No companion test for non-git path | ✅ **RESOLVED** — Test #6 added |

**All 4 previous issues resolved.**

## Prompt Quality: GOOD

The reworded anti-git clause:
- ✅ Targets **raw tool output** (commit hashes, merge logs, push output, diff lines) — not git-as-topic
- ✅ Explicitly preserves legit git tasks: *"even when the ask is about git itself"*
- ✅ Pure-git fallback: *"If the entire message is git activity... produce a short title (e.g., 'Code Merge' or 'Release Prep')"*
- ✅ ALL-CAPS softened → "de-emphasize"
- ✅ Reads naturally with progressive narrowing: unconditional goal framing → conditional de-emphasis → fallback

## Remaining Minor Notes (non-blocking, optional hardening)
1. **🟢 Fallback sentence is untested** — No test asserts "Code Merge"/"Release Prep" is in the prompt. Minor: add `assert "code merge" in prompt.lower()` to test #5.
2. **🟢 Test #6 naming** — Name references "non_git_message" correctly; task context mentioned "Path 1" but the test exercises the shared prompt template (correct behavior, slightly imprecise naming context).
3. **🟢 "ignore" substring** — Test #5 check #2 matches the secondary "Ignore it as a title subject" clause. If that clause is later refined away, the assertion fails even though "de-emphasize" satisfies the intent. Low risk.

**All items are nice-to-have hardening opportunities, not blockers.**
