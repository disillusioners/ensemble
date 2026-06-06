# Bash Tool Backgrounded Subprocess Fix — Test Notes

**Date:** 2026-06-06
**Branch:** `feature/fix-bash-tool-hang-backgrounded-subprocess`
**Status:** ✅ All tests pass

## What Was Tested

Fix for `daemon/tools/bash.py` hanging indefinitely when shell commands backgrounded subprocesses with `&` or `nohup ... &`.

## Key Insight: Test Reproduction

The hang only reproduces with **bare `nohup ... &`** (no explicit redirects). Tests like `nohup cmd > /dev/null 2>&1 &` do NOT reproduce it because explicit redirects prevent pipe FD inheritance. The 3 new tests in `tests/test_tools.py` correctly use the reproducing patterns.

## Fix Mechanism

1. **Temp files replace pipes** — `tempfile.mkstemp` for stdout/stderr/stdin
2. **Process group isolation** — `start_new_session=True` on Unix
3. **Group kill on timeout** — `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` then `SIGKILL`
4. **`_read_file_bytes` helper** — safely reads temp files, handles None/missing paths
5. **Cleanup in all paths** — function-level `finally` block unlinks temp files

## Pre-existing Issue Found (Unrelated)

- `MAX_INSTANCE_HISTORY` default in `daemon/constants.py` was 500 but `config.yaml` and tests expected 300
- Fixed by quick fix commit `0754613`
- Unrelated to the bash tool fix

## Pack Execution

- `tests/test_tools.py` targeted: 35/35 PASS (5s)
- `core_unit_test.sh` broad: 668/668 PASS (17s)
- `ensure.md` dev.sh: PASS (30s stable, exit 124)

## Commits in This Branch

- `9e678b3` — initial fix (temp files + process group)
- `0ad4494` — review feedback fixes
- `a77f2ba` — tidier: extract `_read_file_bytes`, fix stale comments
- `0754613` — quick fix: MAX_INSTANCE_HISTORY default alignment (unrelated)
