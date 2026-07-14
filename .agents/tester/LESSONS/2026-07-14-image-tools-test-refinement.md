# Lesson: Image Tools Test — Shell Fetcher Detection Refinement

Date: 2026-07-14
Branch: feature/image-reader-agent
Pack: image_tools_unit_test

## Issue
The initial test `test_markdown_files_do_not_reference_shell_fetchers` was designed to verify that `agents/image-reader/` markdown files (soul.md, rule.md, workflow.md) don't reference dangerous shell commands (curl, wget, mktemp, rm).

However, `workflow.md` legitimately mentions these commands inside a **prohibition**:
```
Never use shell commands like curl, wget, mktemp, rm to fetch or process images
```

The naive substring check (`"curl" in content`) flagged this as a violation, causing a false positive.

## Root Cause
The test was checking for the mere *presence* of command names, not whether they were being *prescribed* (positive invocation) vs *prohibited* (negative reference).

## Fix
Rewrote the check to use a regex that matches only **positive invocations** — commands followed by flags or arguments:
```
\b(?:curl|wget|mktemp|rm)\s+(?:-{1,2}[a-z]+\s+)*[\S]
```

Added a separate parametrized check that **requires** each markdown file to contain a `Never use` / `must not` / `do not` prohibition statement, validating the security posture rather than just the absence of command names.

## Takeaway
When testing for the absence of dangerous patterns in documentation:
1. Don't use naive substring matching — it catches prohibition mentions as false positives
2. Use regex to distinguish positive invocations from negative references
3. Consider testing for the *presence* of security prohibitions as a positive signal

## Files Changed
- `tests/test_image_tools.py` — test refinement only, no production code changed
PACK_EOF
echo "Lesson written"
