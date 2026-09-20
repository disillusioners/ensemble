# pathlib.Path.glob: single-level bare globs + no brace expansion (grep_files trap)

Date: 2026-09-16
Context: grep_files include-filter non-recursive bug reproduction (RESULTS/2026-09-16-grep-files-include-recursion-repro.md)

## Trap 1 — bare glob is single-level

`Path.glob("*.py")` matches ONLY direct children. Recursion requires `**/*.py`. `daemon/tools/filesystem.py:466` (`glob_pattern = include if include else "**/*"`) only builds the recursive form on the no-include branch — setting `include` silently disabled recursion, returning false "No matches found" whenever the target file sat in a subdirectory.

## Trap 2 — pathlib glob never expands braces

`Path.glob("*.{py,sql}")` and `Path.glob("{*.py,*.sql}")` match ZERO files, at any depth — braces are not glob syntax for pathlib. The grep_files docstring (`filesystem.py:535`) advertises `"*.{js,ts}"`, a syntax that has never worked. Any fix must either implement brace expansion explicitly (e.g. expand to multiple glob patterns) or correct the docstring. Doc-truth lesson: a docstring advertising syntax the engine cannot parse is a live defect even when no test fails.

## Repro heuristic that generalizes

To prove "skip, not depth-limit": search a dir whose ROOT has ZERO files of the include type (subdirs contain them) with a universal pattern (`import`) — a "No matches found" result is impossible-by-construction and is the smoking gun. Beware Python package roots: `__init__.py` at dir root disqualifies the candidate for `*.py`.

## Artifact

Regression pin (RED pre-fix): `tests/unit/test_filesystem_grep_include_recursive_regression.py` — 5 red pins (recursive include ×3, brace recursive ×2), 3 green controls (no-include recursion ×2, missing-path error). Direct-invoke convention: `grep_files.invoke({...})` per `tests/unit/test_filesystem_absolute_path.py:247`.

## RESOLVED 2026-09-16 (commit 3fe667fa, independently verified — RESULTS/2026-09-16-grep-files-fix-verification.md)

Fix adds `_expand_include_glob` (`**/` prepend when pattern lacks `**`; `**`-containing verbatim) + `_expand_single_level_braces` (single-level expansion, nested literal, sorted dedupe). All 6 original rows pass; pin suite 8/8, family 64/64, tool_filter 55/55. Residual documented semantics: in a MIXED brace pattern like `{**/*.ts,*.html}`, the `**` check runs on the whole pattern BEFORE expansion, so bare alternates are NOT prepended — `*.html` stays root-level. Documented in filesystem.py:608-613; pin suite does not cover this mixed form.
