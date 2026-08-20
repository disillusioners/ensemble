# LESSONS: bash `#` comment silently terminates a `\`-continuation chain (pytest flags dropped)

**Date:** 2026-08-20 | **Session:** tool-registry validation fix verification | **Commit:** 68823b49

## Symptom
`tools_suite_unit_test.sh` pack script: added 5 `--deselect` flags for quarantined tests. First re-run reported `5 failed, 671 passed` — the deselects were silently ignored, i.e. the pack "re-ran" identically to the failing run.

## Root Cause
The new flags were appended to an existing multi-line pytest invocation joined by backslash-newline continuations:
```bash
timeout 110s .venv/bin/pytest \
  tests/unit/tools/ \
  --tb=short -q \
  # my comment here            # ← terminates the ENTIRE command at this line
  --deselect ... \
  --deselect ... \
```
In bash, a `#` comment inside a `\`-continued command ends the logical line: the command executes WITHOUT any lines after the comment, and the trailing `\` on the comment line is consumed as part of the comment text. No error is raised — the flags vanish silently.

## Fix
Place ALL comments on standalone lines BEFORE the first line of the continued command (or after the command ends). Verified: pack then deselected 5, PASS.

## Rule of thumb (pack scripts)
1. Comments never inside a continuation chain — always above the command or below it.
2. After editing any pack script, verify the effective pytest argv — e.g. `bash -x test/packs/<pack>.sh 2>&1 | grep -m1 deselect` or confirm the pytest summary line reflects the expected collected/deselected counts.
3. A pack re-run that produces byte-identical failure output after a "fix" is the signature of this bug (or of editing the wrong file) — check argv before suspecting the tests.

## Cross-reference
- Same class of risk applies to any `\`-continued command (timeout/pytest/node), not just pytest packs.
- Pre-existing older pack scripts in this repo use the guarded `|| EXIT_CODE=$?` form; comment placement inside their chains has the same hazard.
