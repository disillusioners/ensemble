# Reasoning-Echo Test Contract Drift + Ambiguous Batch-Replace Hazard

Date: 2026-08-18 · Branch `feature/reasoning-echo-toolcall-gate` @ 9deb9121 (+c80e0232) · Parent e81941ac

## Root Cause 1 — Sibling-suite contract drift
The gate change (3949b8a7) rewrote the echo contract to: model-match AND `bool(tool_calls or tool_call_chunks)` AND reasoning-present. The developer updated `test_reasoning_content_roundtrip.py` (8→16 tests, new TestReasoningEchoToolCallGate) but the two sibling files encoding the OLD contract were left stale:
- `tests/unit/test_reasoning_content_fallback.py` — 6 failures (TestReasoningEchoGating fixtures: no tool_calls, asserted echo)
- `tests/unit/test_reasoning_content_edge_cases.py` — 3 failures (plain conversational turns asserted echo)

Both last touched at 768cbae7 (model-name-gate era). The developer's own targeted run (16/16, 8/8) was green because it only ran the two files they touched — the drift only surfaced under the tester's `-k` sweep. **Lesson for dev handoffs: when a behavioral contract changes, grep the whole test suite for the old contract's assertion pattern (here: `reasoning_content` echo asserts), not just the file being extended.** For testers: always include the sibling suites in the sweep even when the task brief names only two files — this is exactly why the sweep pack exists.

## Root Cause 2 — Ambiguous batch string-replace hazard (worker process note)
During the quick fix, a batch string-replace matched an ambiguous 8-space fixture block that also appeared verbatim in three NEGATIVE model-gate tests (openai/glm/claude), incorrectly arming them with tool calls, while the two actually-intended fixtures (12-space, inside `try:`) were missed → round-1 verification still red. Recovery: `git checkout --` both files (tree was clean at task start) + re-apply via single scripted replace with uniqueness assertions (position-anchored context). Also: parallel `edit_file` calls on the SAME file raced and corrupted the file tail — recovered the same way.
**Rule: when applying N similar edits to one test file, prefer one scripted pass with per-anchor uniqueness assertions over N parallel edit_file calls; verify with `git diff` that exactly the intended semantic changes landed (here: exactly one assertion-semantics flip, rest fixture/docstring additions).**

## Fix Applied (test-only)
- 5 positive fallback tests: tool_calls added to fixtures → positive echo coverage preserved under gate.
- `test_echo_with_tool_calls`: final-answer leg flipped to `is None`.
- 3 edge-case tests: updated-to-negative (plain conversational scenarios; positive coverage deliberately left in fallback.py).
- Commit `c80e0232`, +36/−16, both files. Verification: 35/35 two-file run; 612/612 full sweep re-run; working tree clean.

## Before/After
- Before: sweep 603P/9F (9 stale-contract failures masquerading as regressions)
- After: 612/612 in 102.55s — zero product failures, contract coherently enforced across all three reasoning suites + the 8-test gate class.
