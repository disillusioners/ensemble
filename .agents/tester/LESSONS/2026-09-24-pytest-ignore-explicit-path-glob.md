# pytest `--ignore` is silently ineffective against bash-glob-expanded explicit paths

**Date:** 2026-09-24 · **Gate:** agent-pause-resume-tools acceptance (HALF A pack) · **Cost:** 1 adjudication cycle (no wrong verdict — worker diagnosed in-run)

## What happened
Pack invocation: `.venv/bin/python -m pytest tests/unit/tools/test_[a-m]*.py -q --ignore=tests/unit/tools/test_archive_lifecycle.py`

Bash expanded `test_[a-m]*.py` BEFORE pytest ran, so `test_archive_lifecycle.py` (5 quarantined tests) reached pytest as an **explicit user-requested path**. pytest applies `--ignore` during **rootdir-walk collection only** — never against explicit cmdline paths. Result: the quarantined file ran anyway and its 5 pre-existing failures surfaced, with `--ignore` giving zero warning.

## Proof (worker's collect-only A/B)
- Glob invocation + `--ignore` → 1,061 collected (file still included)
- `pytest tests/unit/tools/test_archive_lifecycle.py --ignore=<same>` → 31 collected
- `pytest tests/unit/tools/ --ignore=<same>` → 2,853 collected (file correctly EXCLUDED — directory walk is where `--ignore` works)

## Rule (pack composition)
When a pack must exclude a file but include a subset of its directory:
1. **Prefer directory + `--ignore`:** `pytest tests/unit/tools/ --ignore=<excluded>` (works), or
2. **Use `--deselect=<file>::<node>`** (works on explicit paths), or
3. If using a glob, **verify the exclusion post-hoc**: `--collect-only -q | grep <excluded-file>` must be empty before trusting the run.

## Detection that saved us
The worker treated "failures all inside the ignored file" as a smell and root-caused it instead of reporting FAIL at face value. Adjudication landed on the existing QUARANTINE.md row — no false branch-caused attribution.
