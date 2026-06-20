# Phase B Testing: OpenCode Filesystem Path Issue

**Date**: 2026-06-20
**Category**: Infrastructure / Environment

## Problem

When spawning opencode sessions for the agents-ensemble project, some sessions cannot access the project directory via the absolute path `/Users/nguyenminkha/All/Code/opensource-projects/agents-ensemble`. The `ls`, `cat`, and `cd` commands return "No such file or directory" even though `find` can locate files at that path.

This appears to be a macOS firmlink / sandbox issue where the `/System/Volumes/Data/` prefix is needed for some access patterns but not others.

## Impact

- Sessions spawned with `working_dir` set to the absolute path may fail to access files
- Sessions that work use relative paths from the CWD (which IS set correctly)
- The `phase-b-core` session worked fine (relative paths), but `sqlite-reg-a` and `sqlite-reg-b` failed (absolute path attempts)

## Workaround

1. **Use the session that works**: If one session succeeds, reuse it for follow-up tasks rather than spawning new ones
2. **Tell sessions to use relative paths**: Instruct "Use relative paths from your current working directory. Do NOT use `cd` with absolute paths."
3. **Check session CWD**: The session's CWD is correctly set even if absolute path access fails

## Lesson

When opencode sessions fail with "directory not found" despite the directory existing, check if the session is using absolute vs relative paths. The CWD is usually correct; only absolute path resolution is broken in some sandbox configurations.
