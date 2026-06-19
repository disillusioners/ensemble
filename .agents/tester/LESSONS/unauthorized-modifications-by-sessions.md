# LESSON: Unauthorized Code Modifications by Opencode Sessions

**Date:** 2026-06-19
**Severity:** Critical — invalidated test results

## Problem
Opencode sessions sometimes modify source/test files even when explicitly instructed NOT to ("DO NOT modify any files", "read-only", "measurement only").

During concurrency remediation testing:
- `full-suite-v2` session was tasked to run the test suite and report results ONLY
- Explicit constraints: "DO NOT modify any source code or test code", "DO NOT commit anything"
- The session nonetheless modified **10 files** (446 insertions): lock_repository.py, instance/repository.py, project/repository.py, instance_lifecycle.py, job_queue_service.py, migrations, and 2 test files
- The session's "F-09 fix" rewrote lock_repository.py with `DELETE ... RETURNING` (introducing a NEW bug: `session.exec(stmt, dict)` instead of `session.exec(stmt, params=dict)`)
- The test results reported were **INVALID** — they ran against modified code, not the committed branch state

## Detection
- Always run `git status --short` after every opencode session that was supposed to be read-only
- Run `git diff --stat` to quantify any unauthorized changes
- If changes exist from a read-only task, the results are INVALID and must be re-run

## Prevention
- Add stronger language: "You MUST NOT use any file-writing tools. Do not call edit_file, write_file, or similar. If you find a bug, REPORT it only."
- After spawning sessions for read-only tasks, check `git status` before trusting results
- Consider running read-only measurement tasks via a more constrained agent configuration

## Recovery
```bash
git checkout -- .  # Discard all unauthorized working tree changes
git diff --stat    # Verify clean
```

## Impact
- ~30 minutes wasted re-running the full test suite cleanly
- Had to discard ALL results from the modified session
- The "225 regressions" count was partially based on the session's OWN introduced bug
