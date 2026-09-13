# Phase 4: Nudge Text Integration (rec 4 in `_build_long_tool_notice`)

Date: 2026-09-14
Author: planner[v2] via plan-creation worker
Parent plan: `.agents/shared/planning/spawn-intelligence-override/plan-overview.md`
Decisions anchor: `decisions.md` D4 (the entire section)
Companion: independent of Phase 1 + Phase 3; loose with Phase 2 on param name.

## Objective

Replace the `# FUTURE` extensibility seam at `daemon/services/long_tool_nudge.py:843-849` with a real rec 4 referencing `model_tier='high'`. Update the test pin at `tests/unit/test_long_tool_nudge.py:546` (currently `assert "# FUTURE" in notice`) to assert the new content. **Both changes land in the same commit** — the Feature #2 settled-zone discipline prohibits splitting.

## Shared Context

- **D4 (decisions.md L169-258):** the 5-section locked structure at `daemon/services/long_tool_nudge.py:796-855` MUST be preserved. Section (a-e) markers stay; only section (d) content changes from placeholder to real recommendation.
- **D4 test-pin update (decisions.md L215-227):** the test pin `tests/unit/test_long_tool_nudge.py:546` (`assert "# FUTURE" in notice`) MUST be replaced with three coordinated assertions.
- **D4 settled-zone discipline (decisions.md L232-239):** the Feature #2 settled zones are: 5-section structure, notice pins (11 assertions in `TestU11NoticeStructure`), kill-switch family `ENSEMBLE_LONG_TOOL_NUDGE_*`, `LongToolNudgeScanner`, `wrapped_tools_node` at `daemon/graph.py:8492-8506`. ALL UNTOUCHED.
- **D4 exact wording (decisions.md L195-202):** the proposed rec 4 wording is locked. Architect review on the test-pin update is the natural gate (Open Question 4).
- **D4 length test (decisions.md L228):** `test_length_within_1_5x_wedge_notice` at `tests/unit/test_long_tool_nudge.py:553-556` may need a threshold relaxation. The added line is ~250 chars; the wedge notice is ~600-800 chars; 1.5x is comfortably wide. Verify in Phase 4.
- **D4 soft wording (decisions.md L249-250):** rec 4 mirrors the existing recs ("Consider ...", "if stuck past 2x threshold"). NO strong wording ("MUST re-spawn").
- **D11 (decisions.md L405-411):** rec 4 mentions ONLY `model_tier='high'`; no `"low"`, no `"medium"`, no `"auto"`.
- **Repo & Dev Environment Conventions blueprint item (d):** tests via `uv run python -m pytest` exclusively.

## Touched Files (with verified anchors at `a904374e`; re-locate by symbol before editing)

| File | Symbol / anchor | Reason |
|------|----------------|--------|
| `daemon/services/long_tool_nudge.py` | `_build_long_tool_notice` function at L796-855; the rec list at L831-842; the `# FUTURE` placeholder at L843-849; the footer at L850-853. | Replace the placeholder with the real rec 4. |
| `tests/unit/test_long_tool_nudge.py` | `TestU11NoticeStructure.test_five_sections_and_no_pause_resume_advice` at L537-551 (the specific pin at L546 is the target). The length test at L553-556. | Update the `# FUTURE` pin to the three new assertions; verify length threshold. |

**Do NOT touch:** the rest of `daemon/services/long_tool_nudge.py` (the scanner, the kill-switch plumbing, the threshold resolver, the eligible-instance detector); `daemon/graph.py:8492-8506` (`wrapped_tools_node`); any file under `daemon/services/llm_load_balancer.py`; the `ENSEMBLE_LONG_TOOL_NUDGE_*` env-var family.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Read `decisions.md` D4 in full + verify the function signature at `daemon/services/long_tool_nudge.py:796-855` and the test pin at `tests/unit/test_long_tool_nudge.py:537-551`. Confirm 3 understanding points before coding: (a) only L843-849 changes, (b) the 4-segment structure (header/why/recs/footer) stays, (c) the test pin update is part of this same commit. | none | Implementer can recite the 5-section labels and their line ranges from memory. |
| 2 | Open `daemon/services/long_tool_nudge.py`, locate L843-849 (the `# FUTURE` placeholder), and the `lines:` list at L819-854 (yes, it's literally a Python list of strings — anchor by `(...)` opening). | Task 1 | Located; `git diff` not started. |
| 3 | Read the 3 existing rec entries at L835-842 to confirm style. The current recs are: | Task 2 | Style understood. |
| 3a | …rec 1: `"1. subtree_messages(parent_id) — see what else the child is currently doing alongside this wedge."` (approximate wording, verify with real file). | 3 | Read. |
| 3b | …rec 2: `"2. send_message the child — it lands at the next turn boundary; a mid-tool child CANNOT receive messages."` (literal from L835-838). | 3 | Read. |
| 3c | …rec 3: `"3. terminate_instance + re-spawn a replacement if stuck past 2x threshold."` (literal from L840-841). | 3 | Read. |
| 4 | Insert the new rec 4 BETWEEN rec 3 (L840-841) and the `# FUTURE` placeholder (L843). The new line replaces BOTH the placeholder comment line AND the explanatory placeholder block. | Tasks 1-3 | New rec 4 in place; placeholder removed. |
| 4a | …exact wording (record verbatim — `grep "Re-spawn with high intelligence" daemon/services/long_tool_nudge.py` MUST return one match): | 4 | Verbatim rec 4 present. |
| 4a | ```\n        (\n            "4. Re-spawn with high intelligence: spawn_instance(agent_id=<role>, "\n            "model_tier='high') — picks the configured high-tier model "\n            "(default 'agentic'). Use after recommendation 3 if the child "\n            "was busy-slow on a low-tier model."\n        ),\n``` | 4 | The trailing comma follows the list convention of the surrounding entries. |
| 4b | …verify the surrounding `lines: list[str] = [` bracket stays OPEN through the new rec 4; the footer at L850-853 still terminates the list. Read the full `lines = [` body once more to confirm. | 4a | `lines` list stays valid; no syntax break. |
| 4c | …verify there is NO `model_tier` mention OUTSIDE the rec 4 entry. The change is string-template only. | 4b | `grep -n "model_tier" daemon/services/long_tool_nudge.py` returns 1 match (the rec 4). |
| 5 | Open `tests/unit/test_long_tool_nudge.py:537-551` and locate the `assert "# FUTURE" in notice  # (d) extensibility seam` line (currently L546). | Task 1 | Located. |
| 5a | …REPLACE that single line with three assertions (exact wording — these are the test pins): | 5 | Three assertions present; old one removed. |
| 5a.i | `assert "model_tier" in notice  # (d) rec 4 names the new opt-in param` | 5 | Pinned. |
| 5a.ii | `assert "high" in notice  # (d) rec 4 names the tier literal` | 5 | Pinned. |
| 5a.iii | `assert notice.count("Re-spawn with high intelligence") == 1  # exactly one rec 4 (no double-implementation)` | 5 | Pinned. |
| 5b | …confirm `(d)` is still labeled in the comment block — change `"# (d) extensibility seam"` to `"# (d) rec 4 re-spawn with high intelligence"`. | 5a | Comment updated. |
| 5c | …confirm the OTHER 5-section pins are UNCHANGED: `# (a) header`, `# (b) why`, `# (c) rec 1`, `# (c) rec 2`, `# (e) footer`. | 5 | `git diff -U0` shows ONLY L546's 1-line replacement (+/-) AND the comment update at the end. |
| 6 | Verify the length test at `tests/unit/test_long_tool_nudge.py:553-556`. Read the threshold, then mentally add ~250 chars for rec 4 to the wedge notice. Decide whether the existing 1.5x threshold passes or needs loosening. | Task 4 | Threshold unchanged or justified loosening documented in the commit. |
| 6a | …if the threshold passes (likely): no test change. | 6 | Add `# no threshold change needed` comment to the commit message body. |
| 6b | …if it fails: bump the multiplier by 0.1 (e.g., 1.5x → 1.6x) AND add a comment explaining why. NEVER loosen by more than 2x of the original. | 6 | Threshold bumped; comment in commit. |
| 7 | Run `uv run python -m pytest tests/unit/test_long_tool_nudge.py -v` and confirm: | Tasks 4-6 | |
| 7a | …`TestU11NoticeStructure.test_five_sections_and_no_pause_resume_advice` is GREEN with the new pins. | 7 | Green. |
| 7b | …`test_length_within_1_5x_wedge_notice` is GREEN (or GREEN post-relaxation). | 7 | Green. |
| 7c | …ALL OTHER `TestU11*` and `TestU12*`-`TestU17*` tests are UNCHANGED-GREEN. | 7 | All green; no other regressions. |
| 8 | Non-regression sweep: `uv run python -m pytest tests/unit -q -k "long_tool_nudge or spawn_intelligence"` to confirm Phase 1 + 4 are co-compatible. | Tasks 4-7 | All green. |

## Test Plan

**Files modified:**
- `daemon/services/long_tool_nudge.py` (1 entry replaces 4 placeholder lines — net +1 line)
- `tests/unit/test_long_tool_nudge.py` (1 `assert` line replaced with 3 + 1 comment line update)

**Run commands (exclusive):**
```bash
# Phase 4 targeted slice
uv run python -m pytest tests/unit/test_long_tool_nudge.py -v

# Phase 4 + Phase 1 + Phase 2 sweep
uv run python -m pytest tests/unit/test_long_tool_nudge.py tests/unit/services/test_spawn_intelligence_tier.py tests/integration/test_spawn_intelligence_tier.py -v
```

**What the (single) updated pin catches:**

- Pin (D4) TestU11 sees `model_tier`, `high`, and exactly one `Re-spawn with high intelligence` phrase.
- All OTHER `TestU11*` assertions stay green — proves the 5-section structure is intact, pause/resume absence is preserved, and the episode-id footer is unchanged.
- The length test (if relaxation applied) proves the notice didn't bloat past readable.

**Test isolation:** no DB, no fixtures, no env mutations — the test is a pure function-call assertion. `tests/unit/test_long_tool_nudge.py:539` calls `_build_long_tool_notice("parent-1", _ctx(), 900)` with a context fixture.

## Non-Regression Checks

- **`LongToolNudgeScanner`:** strictly untouched. `git diff daemon/services/long_tool_nudge.py` shows ONLY the `_build_long_tool_notice` function body change. `grep -n "class LongToolNudgeScanner" daemon/services/long_tool_nudge.py` finds the class definition at its previous anchor — unchanged.
- **Kill-switch family `ENSEMBLE_LONG_TOOL_NUDGE_*`:** untouched. `grep -n "ENSEMBLE_LONG_TOOL_NUDGE" daemon/services/long_tool_nudge.py` shows the same env var reads as before.
- **`wrapped_tools_node` (`daemon/graph.py:8492-8506`):** untouched. `git diff daemon/graph.py` is empty.
- **Resolver (`_resolve_intelligence_tier` from Phase 1):** untouched.
- **Tool surface (`spawn_instance` body from Phase 2):** untouched.
- **5-section structure markers:** the section comments `(a)` / `(b)` / `(c)` / `(d)` / `(e)` in the `_build_long_tool_notice` docstring at L801-811 are NOT changed. The `lines:` list still has the same section ordering; only the (d) entry content changes.
- **Existing tests:** all `TestU11*` / `TestU12*` / `TestU13*` / `TestU14*` / `TestU15*` / `TestU16*` / `TestU17*` UNCHANGED-green. Run the full test_long_tool_nudge.py to prove it.

## Coupling

- **Loose with Phase 2:** the new rec 4 mentions `model_tier='high'` — Phase 2 owns the param literal. Phase 4 is independent of Phase 2 code-shape, but DOES lock on Phase 2's vocabulary. The drift check is `grep -rn "model_tier" daemon/` — both must show consistent vocabulary.
- **Loose with Phase 1:** the new rec 4 is pure template text; it does NOT import the resolver. The resolver's resolution behavior is invisible to the notice.
- **Independent of Phase 3:** docstring vs nudge notice are separate surfaces; either can land first.
- **Independent of Phase 5:** Phase 5 is regression + activation; the notice content has no impact on the default-unchanged behavior or the restart-required activation.

## Risks (phase-specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R4.1 | Test pin update and prod code change land in different commits (split) | High | Medium | Task 5 is in the SAME commit as Task 4. The acceptance gate explicitly requires a single `git commit` covering both file edits. |
| R4.2 | Wording drift from `decisions.md` D4 verbatim spec | Low | Low | Task 4a pins the exact wording; the test pin `assert notice.count("Re-spawn with high intelligence") == 1` ensures the phrase is exact. |
| R4.3 | Length test fails because 1.5x is too tight | Medium | Low | Task 6 forces a pre-emptive length check; relaxation is minor (≤2x of original) and documented. |
| R4.4 | Rec 4 accidentally added as a 6th entry that still keeps the `# FUTURE` placeholder | Medium | Medium | Task 4 explicitly says: REPLACE the placeholder. Pin `assert notice.count("Re-spawn with high intelligence") == 1` enforces singleton. |
| R4.5 | The 5-section `TestU11` pins regress because of incidental whitespace change | Low | Low | Use identical indentation to recs 1-3 (8-space inside the list literal). |
| R4.6 | The function body's `lines: list[str] = [` syntax breaks because of bracket/missing-comma drift | Medium | Low | Task 4b forces re-read of the entire list body; ruff check + pytest will catch syntax errors immediately. |
| R4.7 | Kill-switch family or scanner inadvertently modified | High | Low | `git diff` is constrained to `< 30 lines` total, in the single `lines = [...]` block. Reviewer confirms no other edit. |

## Acceptance Gate

This phase is DONE when:

1. `git log --oneline -1` shows the NEW commit contains BOTH `daemon/services/long_tool_nudge.py` AND `tests/unit/test_long_tool_nudge.py` in the same commit (verify with `git show --stat HEAD`).
2. `git diff HEAD~1 -- daemon/services/long_tool_nudge.py` shows the change is STRICTLY in `lines: list[str] = [` block (within `_build_long_tool_notice`) — zero changes to the function signature, docstring, scanner class, or threshold resolver.
3. `git diff HEAD~1 -- tests/unit/test_long_tool_nudge.py` shows the change is STRICTLY in `TestU11NoticeStructure.test_five_sections_and_no_pause_resume_advice` — zero changes to other test methods.
4. `uv run python -m pytest tests/unit/test_long_tool_nudge.py -v` is 100% green (every `TestU11*` / `TestU12*` ... `TestU17*`).
5. `grep -n "Re-spawn with high intelligence" daemon/services/long_tool_nudge.py` returns exactly 1 line.
6. `grep -n "# FUTURE" daemon/services/long_tool_nudge.py` returns 0 lines (the placeholder is removed).
7. `grep -rn "model_tier" daemon/` returns matches in `daemon/tools/instance.py` (≥3: Pydantic field, runtime kwarg, docstring) AND `daemon/services/long_tool_nudge.py` (1: rec 4) AND `daemon/services/instance_lifecycle.py` (≥1: append_allowed_models tail from Phase 3, if Phase 3 lands first; or 0 if Phase 3 lands after — both acceptable).
8. `grep -n "ENSEMBLE_LONG_TOOL_NUDGE" daemon/services/long_tool_nudge.py` returns the SAME number of matches as before Phase 4 (kill-switch family untouched).
9. `git diff daemon/graph.py` is empty (wrapped_tools_node at L8492-8506 unviolated).

## Exit Criterion

Phase 4 done means: a parent reading the long-tool-call-nudge notice learns about `spawn_instance(model_tier='high')` as the rec-4 option; all Feature #2 settled zones remain UNCHANGED-green; the test pin update is locked in. Phase 5 (regression + activation) can begin independently.
