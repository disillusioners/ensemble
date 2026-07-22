"""Git diff service using subprocess invocations.

Security: Path is pre-validated by WorkspaceGuard before reaching this service.
Git is invoked via subprocess.run() with argument list (never shell=True).
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from daemon.constants import GIT_TIMEOUT_S

logger = logging.getLogger(__name__)


class GitDiffService:
    """Executes git commands via subprocess with timeout and error handling."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._is_repo: bool | None = None  # lazy cache

    async def is_git_repo(self) -> bool:
        """Check if workdir is inside a git repository."""
        if self._is_repo is not None:
            return self._is_repo
        try:
            result = await asyncio.to_thread(
                self._run_git, ["rev-parse", "--is-inside-work-tree"]
            )
            self._is_repo = result.returncode == 0
        except Exception:
            self._is_repo = False
        return self._is_repo

    async def get_file_diff(self, relative_path: str) -> dict:
        """Get diff of a file against HEAD.

        Returns dict with: has_changes, diff, head_content, working_content,
        error (if any).
        """
        if not await self.is_git_repo():
            return {"has_changes": False, "error": "not_a_git_repo"}

        # W4: Use HEAD:./{path} form for pathspec safety. The ``./`` prefix
        # disambiguates a path from a revision name (e.g., a file named
        # ``master`` won't be confused with the ``master`` branch).
        try:
            # git diff HEAD -- <path> — empty output = no changes
            diff_result = await asyncio.to_thread(
                self._run_git, ["diff", "HEAD", "--", relative_path]
            )
            # git show HEAD:./{path} — committed version of the file
            head_result = await asyncio.to_thread(
                self._run_git, ["show", f"HEAD:./{relative_path}"]
            )
        except Exception as e:
            logger.warning("Git subprocess error for %s: %s", relative_path, e)
            return {"has_changes": False, "error": f"git_error: {e}"}

        # W6: Diff output size limit (same 1MB budget as file content)
        diff_text = diff_result.stdout
        if len(diff_text) > 1_048_576:
            return {
                "has_changes": True,
                "diff": "(diff too large to display)",
                "head_content": None,
                "working_content": None,
                "error": "diff_too_large",
            }

        # File is new (not in HEAD) — git show returns non-zero
        head_content = head_result.stdout if head_result.returncode == 0 else None
        has_changes = bool(diff_text.strip()) or head_content is None

        # Read working tree content for the "b" side of the merge view.
        working_content = None
        working_file = self.workdir / relative_path
        try:
            working_content = working_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            working_content = None  # binary or deleted — UI handles gracefully

        return {
            "has_changes": has_changes,
            "diff": diff_text if has_changes else None,
            "head_content": head_content,
            "working_content": working_content,
            "error": None,
        }

    def _run_git(self, args: list[str]):
        """Synchronous git subprocess call. Called via asyncio.to_thread.

        W7: All git exceptions (TimeoutExpired, FileNotFoundError, OSError)
        are caught by the caller's try/except. Returns CompletedProcess even
        on non-zero exit (returncode is checked by the caller).
        """
        return subprocess.run(
            ["git"] + args,
            cwd=self.workdir,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )
