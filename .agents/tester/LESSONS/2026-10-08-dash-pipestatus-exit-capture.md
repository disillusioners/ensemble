# Lesson: daemon shell is dash — `${PIPESTATUS[0]}` exit-code capture fails in dispatch templates

**Date:** 2026-10-08 · **Commission:** compaction-never-blocked gate · **Cost:** 3 workers each had to re-run pipelines under `bash -c` to capture authoritative exit codes.

## Symptom
Dispatch-template command lines ending in `echo "EXIT:${PIPESTATUS[0]}"` fail with `/bin/sh: 1: Bad substitution` (exit 2 at the shell level) — the tool shell for worker `bash` invocations is **dash** (`/bin/sh`), not bash. The pipeline itself (pytest/pack) runs fine; only the exit-code echo breaks, producing a misleading tool-level exit 2 that looks like a failure.

## Fix (template hygiene)
- In future strict single-pack templates, wrap the whole pipeline for exit capture: `bash -c 'cd <wt> && timeout 300 bash <pack> 2>&1 | tee <log>; echo "EXIT:${PIPESTATUS[0]}"'`
- Or avoid the pipe entirely: run `timeout 300 bash <pack> > <log> 2>&1; echo "EXIT:$?"` (plain `$?` works in dash).
- Never adjudicate a pack result off the tool-level exit code alone when the template used `PIPESTATUS` — read the pack's own `RESULT:` line and the pytest tally from the tee'd log.

## Also observed same gate
- `uv` is not on the dash PATH; absolute path `/home/nea/.local/bin/uv` works.
- `.pytest_cache` avoidance: `-p no:cacheprovider` on direct pytest invocations kept the worktree porcelain fully empty (pack scripts' own cache behavior aside).
