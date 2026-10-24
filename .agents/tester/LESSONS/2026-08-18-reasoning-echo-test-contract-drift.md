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

> **2026-10-23 update:** the same-file `edit_file` race leg of this lesson is fixed at the tool
> level (`feature/edit-file-race-fix`: per-canonical-path serialization + atomic replace +
> read-back verify; see `2026-08-30-edit-file-same-file-parallel-batch-race.md`). Activation
> pending — until the daemon is rebuilt on a commit containing the fix, the Rule above stands.
> The scripted-pass preference remains sound regardless (it also guards against the ambiguous
> anchor-match failure documented above, which the tool-level fix does NOT address).
>
> **Scope (corrected 2026-10-23):** the in-process fix covers writers that address the file
> through the same canonical, CASE-NORMALIZED spelling. On a case-insensitive filesystem
> (macOS default volumes) `Config.txt` and `config.txt` are ONE inode; before 2026-10-23 the lock
> registry keyed on `realpath`, which does NOT normalize case, so the two spellings took two
> locks and raced into a lost write (both calls reporting SUCCESS). The lock key is now
> case-normalized on case-insensitive filesystems, so both spellings share ONE lock. Genuinely
> distinct files are unaffected; on a case-SENSITIVE macOS volume, `a.txt` and `A.txt` now share
> one lock (throughput loss only, no correctness loss).
>
> **Counts (measured 2026-10-23, scope stated):** the new
> `tests/unit/test_filesystem_write_race_fix.py` measured against the TRUE pre-fix release base
> (this branch's merge base — "chore(release): bump version to 0.27.5"; no SHA is quoted here
> because a branch-local SHA dangles the moment the commit is amended — SHA facts live in the
> commit messages):
>
> * The 33-test file as shipped on the branch: 11-13 failed / 19-21 passed / 1 skipped against
>   the true base (scheduler-dependent). Two independent measurements: the tester recorded
>   11-13F / 19-21P / 1S, the reviewer recorded 12F / 20P / 1S over 7 of 8 runs.
> * HISTORICAL — the ORPHANED pre-review 27-test revision of the file measured 9-11 failed /
>   15-17 passed / 1 skipped over 12 runs (partly deterministic). That revision was amended and is
>   GONE — its numbers are NOT current.
> * Non-vacuity anchor: against the ORPHANED pre-review revision's source, the expanded file
>   measured 5 failed / 28 passed (3/3 runs, byte-identical failed set).
>
> The base-leg failures are only PARTLY deterministic (one named barrier test can pass on the base
> leg by scheduling luck), so quote the range, never a fixed denominator. The mid-branch pins (the
> edit_file CRLF read-back false-ERROR and the case-variant lock key) are REGRESSION GUARDS for
> defects introduced by this branch — NOT base-leg discriminators: each is mutation-proved
> non-vacuous, and 4 of the 6 are green on the true base BY DESIGN (the base has no read-back
> verification at all, so the defect class does not exist there).

## Fix Applied (test-only)
- 5 positive fallback tests: tool_calls added to fixtures → positive echo coverage preserved under gate.
- `test_echo_with_tool_calls`: final-answer leg flipped to `is None`.
- 3 edge-case tests: updated-to-negative (plain conversational scenarios; positive coverage deliberately left in fallback.py).
- Commit `c80e0232`, +36/−16, both files. Verification: 35/35 two-file run; 612/612 full sweep re-run; working tree clean.

## Before/After
- Before: sweep 603P/9F (9 stale-contract failures masquerading as regressions)
- After: 612/612 in 102.55s — zero product failures, contract coherently enforced across all three reasoning suites + the 8-test gate class.
