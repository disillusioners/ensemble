# edit_file: Never Batch Multiple Same-File Edits in One Parallel Block

**Date:** 2026-08-30 · **Found during:** security-boundary-hygiene re-gate records update

## Root cause
Multiple `edit_file` calls targeting the **same file**, issued in one tool block (which may execute
concurrently), race: each call does read-modify-write of the whole file. A call holding a stale
snapshot overwrites edits from sibling calls that landed in between. Symptoms observed:

- Edits reporting `SUCCESS` yet absent from the file afterward.
- Same edit confirmed present by one grep, then "reverted" moments later (caught mid-race).
- One call failing `String not found` for text that exists (ran against a pre-sibling snapshot).
- Pack row edits surviving while the same batch's other edits to the same file vanished.

This project already carries a "torn-write anomaly" fence note — concurrent writers to shared md
files (PACKS.md especially) are a known hazard. The tester adding its own parallel writer to the
same file reproduces the class.

## Rule
1. **One edit_file call per file per tool block.** Sequence additional edits to the same file
   across separate turns, or
2. **Use a single-process script** (python heredoc via bash — allowed for `.agents/tester/` file
   ops) applying all transforms + verifying markers in one atomic sequential pass.

## Fix pattern (used successfully)
`rw()` helper: read once → apply conditional replaces (skip if `new in text`, flag if anchor
missing) → write once → assert every expected marker in the same process. Re-ran after the race;
12/12 verification checks green.

## Blast radius if uncaught
Gate verdicts could silently read FAIL after a PASS flip (or vice versa) — a records-integrity
defect in exactly the artifact merge decisions are made from. Always grep-verify after writes.

## 2026-10-23 UPDATE — root cause fixed at the tool level (activation pending)
`feature/edit-file-race-fix` fixes the class in `daemon/tools/filesystem.py`: (1) per-canonical-path
serialization shared by `edit_file` AND `write_file` (same-file calls from one parallel block
serialize; different files stay parallel); (2) atomic writes (same-directory temp file +
`os.replace`, mode preserved, temp cleaned on failure); (3) read-back verification — `SUCCESS` now
means the replacement is provably in the file, and a mismatch returns an honest `ERROR`.

**Scope of the claim** (corrected 2026-10-23): the "SUCCESS yet absent" phantom above is no longer
reachable from in-process writers **that address the file through the same canonical,
case-normalized spelling**. Two spellings of ONE file are the same write: on a case-insensitive
filesystem (macOS default volumes) `Config.txt` and `config.txt` are ONE inode, and the lock
registry keyed on `realpath` — which resolves symlinks but does NOT normalize case — so the two
spellings took TWO locks and raced into a lost write with both calls reporting SUCCESS
(live-reproduced on Darwin, 170/400). The lock key is now case-normalized on case-insensitive
filesystems, so both spellings share ONE lock. Genuinely distinct files are unaffected; on a
case-SENSITIVE volume on macOS, `a.txt` and `A.txt` now share one lock — over-serialization
(throughput loss only, no correctness loss).

Base-leg proof (counts measured 2026-10-23, scope stated). The new
`tests/unit/test_filesystem_write_race_fix.py` measured against the TRUE
pre-fix release base (this branch's merge base — "chore(release): bump version
to 0.27.5"; no SHA is quoted here because a branch-local SHA dangles the
moment the commit is amended — SHA facts live in the commit messages):

* The 33-test file as shipped on the branch: **11-13 failed / 19-21 passed /
  1 skipped** against the true base (scheduler-dependent). Two independent
  measurements: the tester recorded 11-13F / 19-21P / 1S, the reviewer
  recorded 12F / 20P / 1S over 7 of 8 runs.
* HISTORICAL — the ORPHANED pre-review 27-test revision of the file measured
  9-11 failed / 15-17 passed / 1 skipped over 12 runs (10F/16P/1S in 10 runs,
  11F/15P/1S in 1 run, 9F/17P/1S in 1 run). That revision was amended and is
  GONE — its numbers are NOT current and must never be quoted as such.
* Non-vacuity anchor: against the ORPHANED pre-review revision's source, the
  expanded file measured 5 failed / 28 passed (3/3 runs, byte-identical failed
  set).

The base-leg failures are only PARTLY deterministic: the failures raised as
`AttributeError` on branch-only symbols are scheduler-independent, but the
named barrier test `test_barrier_two_threads_both_replacements_present` can
PASS on the base leg by scheduling luck. Do not quote a base-leg count as a
fixed denominator — quote the range and the scope.

The mid-branch pins (the edit_file CRLF read-back false-ERROR and the
case-variant lock key) are REGRESSION GUARDS for defects introduced by this
branch — NOT base-leg discriminators. Each is mutation-proved non-vacuous (a
targeted mutation kills it), and 4 of the 6 are green on the true base BY
DESIGN: the base has no read-back verification at all, so the defect class
does not exist there.

Full-suite framing: **208 passed** is EXACT for the 7 named filesystem-tool
files (`tests/unit/test_filesystem_absolute_path.py`,
`tests/unit/test_filesystem_grep_include_recursive_regression.py`,
`tests/unit/test_filesystem_walk_guardrails.py`, `tests/unit/test_filesystem_workdir.py`,
`tests/test_tools.py`, `tests/test_prompt_artifact_guards.py`,
`tests/unit/tools/test_dynamic_toolset_expansion.py`). Add the race-fix file
its own node count for the total — at this commit that file is 42 tests
(42/42 green), so the sum is 208 + 42 = **250**. Never claim 261: that
figure is a counting artifact (it double-counts a file's static function
count and omits another file from the selection).

**Until the running daemon is rebuilt on a commit containing this fix, the Rule above still
applies verbatim** — the fix is inert in any older binary. After activation, Rule 1/2 become
unnecessary for in-process safety (same-block same-file edits are safe); the scripted-pass pattern
remains good hygiene for large multi-anchor doc edits, and the cross-process writer fence note
(the "torn-write anomaly") is NOT covered by the in-process lock — external writers to shared md
files are still a hazard. Grep-verify discipline stays recommended.
