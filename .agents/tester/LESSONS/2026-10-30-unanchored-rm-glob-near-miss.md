# Near-miss: an unanchored `rm` glob against a directory holding committed evidence

**Date:** 2026-10-30 (eventloop harness round-2b)
**What happened:** while clearing transient harness outputs I ran
`rm -f …/raw/BASELINE.*.json …/raw/run_*.out`. The directory also holds the
COMMITTED pre-repair baseline artifacts (`BASELINE.full.json`,
`run_full.out`) — both were deleted from the working tree (unstaged `D`).
**Caught immediately** by the post-command `git status` discipline and
restored with `git checkout --`; blob-identity proven afterwards
(`a830f933…` before == after).
**Rule (already a critical note for test scripts; this extends it to
interactive shell):** never use a wildcard `rm` against a directory that
mixes committed and transient artifacts. Either delete by explicit name, or
gate the glob through `git ls-files` first, e.g.
`git clean -n` then `git clean -f -- <path>` for untracked-only deletion.
The `.gitignore` split (committed evidence vs ignored run outputs) is the
contract; an unanchored `rm` ignores it by construction.
