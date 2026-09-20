# Test Report: grep_files include-filter non-recursive bug — reproduction + red regression pin

Date: 2026-09-16
Worker Instance: 2a20d4b4-e9d7-489d-92f9-e16f2cd3e76a (grep-files-repro, no load_skill — infrastructure/test-creation dispatch)
Branch: `fix-grep-files-recursive-include` (pre-fix state)
Status: BUG CONFIRMED — 4/4 repro rows reproduce; regression pin filed RED (5 red / 3 green controls). Tool NOT fixed. Test NOT committed (per leader instruction — parallel developer fix in flight).

## Summary

- Bug: `grep_files` becomes silently NON-recursive when `include` is set. Without `include`, recursion works.
- Verdict: **CONFIRMED**. Rows 1, 2, 3, 5 reproduce; rows 4 and 6 (controls) pass.
- Deliverable: `tests/unit/test_filesystem_grep_include_recursive_regression.py` (8 tests: 5 RED expected-failure pins, 3 GREEN controls), family `tests/unit/test_filesystem_*.py`.
- Sibling sub-finding (🟠 important): brace include syntax (`*.{py,sql}`, `{*.py,*.sql}`) matches **zero files even at root** — `pathlib.Path.glob` does not expand braces, yet the tool docstring (`daemon/tools/filesystem.py:535`) advertises `"*.{js,ts}"`.
- Root-cause pointer for the parallel developer (worker located it during invocation setup; diagnosis remains the developer's): `daemon/tools/filesystem.py:466` — `glob_pattern = include if include else "**/*"` passes the bare user glob to `Path.glob()`; bare `"*.py"` is single-level (recursive needs `"**/*.py"`).

## Implementation + invocation

- `def grep_files` at `daemon/tools/filesystem.py:446` (decorators at :444; `@tool`-wrapped — original callable at `grep_files.func`).
- Direct invocation used (family convention from `tests/unit/test_filesystem_absolute_path.py:247`): `grep_files.invoke({"pattern", "path", "include", "workdir": None, "case_sensitive", "whole_word", "offset", "limit"})` with absolute paths (bypasses workdir gate). No HTTP layer.

## Reproduction matrix (repo-adapted; original user paths tabs/projects/camcheck4 do not exist here)

| Row | Path | Include | Pattern | Actual | Expected | Verdict |
|-----|------|---------|---------|--------|----------|---------|
| 1 | `daemon/` | `*.py` | `EXECUTOR_ENV_ALLOWLIST` (verified unique to `daemon/tools/upgrade_journal.py`) | `No matches found for: EXECUTOR_ENV_ALLOWLIST` | subdir hit | 🔴 REPRODUCED |
| 2 | `daemon/` | `{*.py,*.sql}` | `def ` | `No matches found for: def ` | root+subdir hits | 🔴 REPRODUCED (brace also dead at root — sibling defect) |
| 3 | tmp tree: root has 0 `.py`, subdirs `inner/`, `inner/deeper/` have `.py` | `*.py` | `import` | `No matches found for: import` | subdir hits | 🔴 REPRODUCED (smoking gun — impossible result) |
| 4 | same tmp tree | (none) | `import` | both nested files returned | recursive hits | ✅ CONTROL-OK |
| 5 | `daemon/` | `*.py` | `import` (limit 50) | 50 hits, ALL root-level single-segment paths, 0 subdir | subdir hits present | 🔴 REPRODUCED (skip, not depth-limit) |
| 6 | `/tmp/definitely_missing_dir_xyz_12345` | `*.py` | `import` | `ERROR: Path does not exist: ...` | proper error | ✅ CONTROL-OK |

## Regression pin (uncommitted — intentional)

File: `tests/unit/test_filesystem_grep_include_recursive_regression.py` (250 lines, 8 tests, 4 classes)

| Test | Status |
|------|--------|
| `TestIncludeRecursivePy::test_include_py_finds_nested_deep_py_hit` | ❌ RED (case a) |
| `TestIncludeRecursivePy::test_include_py_finds_two_levels_deep_hit` | ❌ RED (case a) |
| `TestIncludeRecursivePy::test_include_py_ignores_non_py_files_at_subdir` | ❌ RED (case a) |
| `TestNoIncludeBaseline::test_no_include_finds_nested_hit` | ✅ GREEN (case b control) |
| `TestNoIncludeBaseline::test_no_include_finds_two_levels_deep_hit` | ✅ GREEN (case b control) |
| `TestIncludeBraceRecursive::test_brace_form_finds_nested_py_and_txt` | ❌ RED (case c, `{*.py,*.txt}`) |
| `TestIncludeBraceRecursive::test_alt_brace_form_finds_nested_py` | ❌ RED (case c, `*.{py,txt}`) |
| `TestNonExistentPath::test_missing_dir_returns_error` | ✅ GREEN (case d control) |

Red proof: `timeout 300 uv run python -m pytest tests/unit/test_filesystem_grep_include_recursive_regression.py -v` → **5 failed, 3 passed in 0.27s**; `--collect-only -q` clean (8 collected). Every red failure is the documented symptom ("No matches found" while subdirs contain the token) — no unexpected pass, no flake. Module docstring declares RED-is-expected until the fix lands; brace tests carry comments stating they pin recursion + brace support and may need include-string updates if the fix intentionally changes documented syntax.

Note: family is purely hermetic (tmp_path); no prior grep_files content-behavior test existed (only registry mention in `tests/test_tool_filter.py`), so the repo-real assertion was skipped per task spec's conditional.

## Constraints honored

- Zero edits under `daemon/` or any source; no fix applied; no commit/stage/push.
- Only repo write = the one test file. Scratch scripts in `/tmp`.
- All pytest via `uv run python -m pytest` from repo root (bare pytest broken on this machine); timeout-wrapped.

## Foreign working-tree edits (disclosed by worker, untouched)

- ` M .agents/tidier/notes.md` (append re feature/fix-source-edit-agent, unrelated)
- Untracked: `.agents/shared/planning/leader-completion-attestation/resolver-unification.md`, 3× `.agents/tester/RESULTS/2026-09-14-*.md`, `test/packs/fe_edit_agent_e2e_test.sh` + harness/mock, `test/packs/fe_unit_full_test.sh`, and the new test file itself.

## Action Needed

- [ ] Developer (parallel instance): land the recursion fix at `daemon/tools/filesystem.py:466` seam; decide brace strategy (expand braces OR fix docstring :535 to stop advertising `*.{js,ts}`) — the red brace pins encode "expand braces"; if the decision is docstring-only, update those two tests' include strings per their comments.
- [ ] After fix: re-run the pin file (expect 8/8 green), then commit fix + test together; register any new pack script in PACKS.md if one is introduced (this file is a plain pytest unit test, not a pack).

## Documentation Updated

- [x] RESULTS/2026-09-16-grep-files-include-recursion-repro.md — this report
- [x] LESSONS/2026-09-16-pathlib-glob-recursive-include-and-braces.md — pathlib glob trap
- [ ] PACKS.md — no pack introduced (plain pytest regression pin)
