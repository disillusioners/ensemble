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
