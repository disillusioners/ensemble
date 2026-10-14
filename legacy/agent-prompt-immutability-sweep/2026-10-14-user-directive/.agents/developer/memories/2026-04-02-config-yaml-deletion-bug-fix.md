# config.yaml Deletion Bug Fix

**Date:** 2026-04-02
**Commit:** 3b45780

## Problem
`config.yaml` kept getting deleted from the project root. Git history showed repeated recovery commits (ca00ac1 "dont remove config.yaml", be17070 "config yaml recover").

## Root Cause
`tests/test_config.py` had two tests using `Path("./config.yaml")` with `.unlink()`:
- `test_load_config_default` (line 72→90): wrote test data to real config, deleted in `finally` cleanup
- `test_missing_default_config_file` (line 166→168): unconditionally deleted config if it existed

No pytest config existed, so cwd = project root, making `./config.yaml` resolve to the real file.

## Fix
- Changed both tests to use `tmp_path` fixture instead of `Path("./config.yaml")`
- Tests set `ENSEMBLE_CONFIG` env var to point `load_config()` at temp directory
- `daemon/config.py` already supports `ENSEMBLE_CONFIG` env var (line 164)

## Key Learning
- When `load_config()` or similar functions use env vars or configurable paths, tests should mock via env var rather than trying to replace the actual file
- Always check if there's an env var override before monkeypatching module-level constants
