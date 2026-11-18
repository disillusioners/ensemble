SONIC-MODULE-BRIEF — release-bump (M4, slot 4)
TASK: Bump version 0.31.2 -> 0.31.3 in this release-cut worktree. NO tag (integration owns it), NO push.
STEPS:
1. export PATH=/home/nea/.local/bin:$PATH ; uv sync (worktree .venv ONLY — never main .venv, no bare python).
2. .venv/bin/python scripts/bump_version.py — runs ONCE ONLY (NON-IDEMPOTENT: re-run bumps again). It edits
   pyproject.toml, daemon/__init__.py, tests/test_api.py and runs uv sync (uv.lock). If it dies mid-way: recover
   with git checkout -- <affected> and re-run ONCE; report any partial state honestly.
3. Verify: grep version strings in the 4 files (pyproject `version = "0.31.3"`, daemon/__init__.py __version__,
   tests/test_api.py expectation, uv.lock entry) and `git status --porcelain` shows ONLY the expected files.
4. CHANGELOG.md (yours ALONE in this commission): read the top; if the first UNDATED `## [Unreleased]` heading
   lacks a guard-fix entry, add one concise bullet under it (Fixed: agent tool-lane systemctl guard intent-match
   inversion — composed restart/stop of ensemble units refused, read-only inspection allowed; proc lane covered;
   incident 2026-11-18 wi-a5ec5b3e). Then rename that first undated heading to `## [v0.31.3] — 2026-11-18`.
   NEVER touch the HISTORICAL dated `## [Unreleased] — <date>` headings deeper in the file. Verify heading order
   post-edit: (fresh Unreleased optional) -> v0.31.3 -> v0.31.2 -> older.
5. Commit PATHSPEC-LIMITED: git add pyproject.toml daemon/__init__.py tests/test_api.py uv.lock CHANGELOG.md &&
   git commit --only -m "chore(release): bump version to 0.31.3" -- pyproject.toml daemon/__init__.py tests/test_api.py uv.lock CHANGELOG.md
FILES (yours ONLY): pyproject.toml, daemon/__init__.py, tests/test_api.py, uv.lock, CHANGELOG.md.
NEVER: tag, push, git add -A, git stash, touch daemon/tools/** or tests/unit/** (M1's), restart/stop anything.
REPORT: [sonic-module-report-v2] — commit sha, 4-site version verification, CHANGELOG heading verification, risks.
