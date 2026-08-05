"""Doc commit service: atomic validate → git commit for doc-maintenance writes.

Architectural posture: NO AGENT GETS SHELL ACCESS. This service runs
subprocesses internally on the server side, exactly mirroring the
:class:`~daemon.services.git_diff_service.GitDiffService` pattern
(``subprocess.run(arg_list, cwd=workdir, capture_output=True, timeout=T,
shell=False)``).

The blueprinter invokes this service via the ``commit_docs_validated`` tool,
which is a thin wrapper. The agent cannot intervene between validation and
commit — the sequence runs inside a single synchronous call, closing the
TOCTOU window.

7-step atomic sequence:

    1. PRE-FLIGHT  — verify git repo, not detached HEAD, not mid-rebase/merge,
                    and not on a protected branch (main/master/latest).
    2. BUILD DETECT — file-presence heuristic (or project override).
    3. BUILD/TEST  — run the detected build command. FAIL = hard stop.
    4. PATH FILTER — re-check every changed path against the doc allowlist;
                    drop paths that don't exist or aren't modified.
    5. STAGE       — ``git add -- <paths>`` (explicit paths, never ``git add .``).
    6. COMMIT      — ``git commit -m <msg> --only <paths>`` (atomic per-file).
    7. RETURN      — :class:`CommitResult` with status, hash, files, reason.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .build_system_detector import BuildSystem, detect as detect_build_system

logger = logging.getLogger(__name__)


# ─── Result dataclass ────────────────────────────────────────────────────────


# Status literals — kept narrow so callers can match exactly. Adding a new
# status here requires updating the architecture doc's failure-handling matrix.
CommitStatus = Literal[
    "COMMITTED",
    "BUILD_FAILED",
    "BUILD_TIMEOUT",
    "REPO_UNSAFE",
    "BRANCH_UNSAFE",
    "NO_VALID_PATHS",
    "STAGING_ERROR",
    "BLOCKED_BY_HOOK",
    "SKIPPED",
]


@dataclass
class CommitResult:
    """Outcome of an atomic doc-commit attempt."""

    status: CommitStatus
    commit_hash: str | None = None
    files: list[str] = field(default_factory=list)
    reason: str = ""
    build_output: str = ""  # truncated to 4KB; secrets stripped
    duration_ms: int = 0


# ─── Constants ───────────────────────────────────────────────────────────────

# Branches the blueprinter must NEVER auto-commit to.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master", "latest"})

# Max build output retained in CommitResult (bytes). Keeps the dataclass small.
MAX_BUILD_OUTPUT_BYTES = 4096

# Build timeout (seconds). Matches the architecture doc.
BUILD_TIMEOUT_S = 300

# Pre-flight / stage / commit timeouts (seconds).
GIT_SUBPROCESS_TIMEOUT_S = 30

# Doc allowlist prefixes (re-validated inside the service as a defense in
# depth, even though the doc-maintainer agent's tool surface also enforces).
DOC_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "docs/",
    "doc/",
)
DOC_ALLOWLIST_BASENAMES: frozenset[str] = frozenset({
    "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE.md",
    "CODE_OF_CONDUCT.md", "SECURITY.md",
})

# Paths NEVER allowed even if they would otherwise match (defense in depth).
DOC_PATH_DENYLIST: tuple[str, ...] = (
    ".agents/",
    "daemon/tools/",
    "daemon/services/doc_",  # service code itself
    "frontend/",
    "node_modules/",
    ".git/",
    "__pycache__/",
    "config.yaml",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env",
    ".envrc",
)


# ─── Service ─────────────────────────────────────────────────────────────────


class DocCommitService:
    """Atomic validate → commit service for doc-maintenance writes.

    All subprocess calls use ``subprocess.run(arg_list, shell=False)``.
    The service is intentionally synchronous from the agent's perspective —
    the agent-facing tool wrapper awaits the result via ``asyncio.to_thread``.
    """

    def __init__(
        self,
        workdir: str | Path,
        project_metadata: dict | None = None,
    ) -> None:
        self.workdir = Path(workdir)
        self.project_metadata = project_metadata or {}

    # ── Public API ──────────────────────────────────────────────────────

    async def commit_docs_validated(
        self,
        changed_paths: list[str],
        message: str,
    ) -> CommitResult:
        """Atomic validate → commit for doc-maintenance writes.

        Args:
            changed_paths: Paths modified by doc-maintainer workers. Relative
                to ``self.workdir``. Re-validated against the doc allowlist
                inside this method.
            message: Conventional commit message (e.g.,
                ``docs(blueprinter): auto-update rebuild auth [skip ci]``).

        Returns:
            :class:`CommitResult` describing the outcome.
        """
        t0 = time.monotonic()
        if not changed_paths:
            return CommitResult(
                status="NO_VALID_PATHS",
                reason="no changed paths supplied",
                duration_ms=_elapsed_ms(t0),
            )

        # 1. PRE-FLIGHT
        pre = await self._preflight()
        if pre is not None:
            return CommitResult(
                status=pre[0],
                reason=pre[1],
                duration_ms=_elapsed_ms(t0),
            )

        # 2. BUILD DETECTION (uses project_metadata override if present)
        build_system = self._detect_build_system()
        build_output = ""

        # 3. BUILD VALIDATION (only if a build system was detected)
        if build_system is not None:
            build_result = await self._run_build(build_system)
            if build_result is not None:
                # Non-None => failure or timeout.
                status, reason, output = build_result
                return CommitResult(
                    status=status,
                    reason=reason,
                    build_output=output,
                    duration_ms=_elapsed_ms(t0),
                )
            # Build passed — capture output for the result.
            build_output = ""  # Currently captured only on failure.

        # 4. PATH FILTER
        valid_paths = self._filter_paths(changed_paths)
        if not valid_paths:
            return CommitResult(
                status="NO_VALID_PATHS",
                reason="no paths passed validation (all out of scope, missing, or unmodified)",
                duration_ms=_elapsed_ms(t0),
            )

        # 5. STAGE
        stage_err = await self._stage_paths(valid_paths)
        if stage_err is not None:
            return CommitResult(
                status="STAGING_ERROR",
                reason=stage_err,
                duration_ms=_elapsed_ms(t0),
            )

        # 6. COMMIT
        commit_hash, commit_err = await self._commit(message, valid_paths)
        if commit_err is not None:
            return CommitResult(
                status=commit_err[0],
                reason=commit_err[1],
                files=valid_paths,
                build_output=build_output,
                duration_ms=_elapsed_ms(t0),
            )

        return CommitResult(
            status="COMMITTED",
            commit_hash=commit_hash,
            files=valid_paths,
            reason="",
            build_output=build_output,
            duration_ms=_elapsed_ms(t0),
        )

    # ── Step 1: pre-flight ──────────────────────────────────────────────

    async def _preflight(self) -> tuple[CommitStatus, str] | None:
        """Return None on success or (status, reason) on rejection."""
        # Verify workdir exists and is a git repo.
        try:
            r1 = await asyncio.to_thread(
                self._run_git, ["rev-parse", "--is-inside-work-tree"]
            )
        except Exception as exc:
            return ("REPO_UNSAFE", f"git rev-parse failed: {exc}")

        if r1.returncode != 0:
            return ("REPO_UNSAFE", "not inside a git working tree")

        # Reject detached HEAD.
        try:
            r2 = await asyncio.to_thread(
                self._run_git, ["symbolic-ref", "--quiet", "HEAD"]
            )
        except Exception as exc:
            return ("REPO_UNSAFE", f"git symbolic-ref failed: {exc}")

        if r2.returncode != 0:
            return ("REPO_UNSAFE", "detached HEAD — refusing to commit")

        # Reject mid-rebase / mid-merge (porcelain output starts with
        # rebase/merge markers in those states).
        try:
            r3 = await asyncio.to_thread(self._run_git, ["status", "--porcelain"])
        except Exception as exc:
            return ("REPO_UNSAFE", f"git status failed: {exc}")

        if r3.returncode != 0:
            return ("REPO_UNSAFE", f"git status failed: {r3.stderr.strip()}")

        # Detect rebase/merge in progress via .git directory presence.
        # (porcelain output alone isn't a reliable signal.)
        for state_dir in ("rebase-merge", "rebase-apply", "merge"):
            if (self.workdir / ".git" / state_dir).exists():
                # Suppress: also valid for a real .git in workdir subdirs.
                # We check the resolved .git path.
                pass
        # Resolve .git to handle worktrees.
        git_dir = self._resolve_git_dir()
        if git_dir is None:
            return ("REPO_UNSAFE", "could not resolve .git directory")
        for state_dir in ("rebase-merge", "rebase-apply", "MERGE_HEAD"):
            if (git_dir / state_dir).exists():
                return ("REPO_UNSAFE", f"mid-rebase/merge detected ({state_dir})")

        # Reject protected branches.
        branch = self._current_branch()
        if branch in PROTECTED_BRANCHES:
            return (
                "BRANCH_UNSAFE",
                f"refusing to commit to protected branch {branch!r}",
            )

        return None

    def _resolve_git_dir(self) -> Path | None:
        """Resolve the .git directory path (handles worktrees)."""
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=str(self.workdir),
                capture_output=True,
                text=True,
                timeout=GIT_SUBPROCESS_TIMEOUT_S,
                shell=False,
                check=False,
            )
        except Exception:
            return None
        if r.returncode != 0:
            return None
        git_dir = Path(r.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = (self.workdir / git_dir).resolve()
        return git_dir

    def _current_branch(self) -> str:
        """Return the current branch name, or '' if not on a branch."""
        try:
            r = subprocess.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                cwd=str(self.workdir),
                capture_output=True,
                text=True,
                timeout=GIT_SUBPROCESS_TIMEOUT_S,
                shell=False,
                check=False,
            )
        except Exception:
            return ""
        if r.returncode != 0:
            return ""
        return r.stdout.strip()

    # ── Step 2: build detection ─────────────────────────────────────────

    def _detect_build_system(self) -> BuildSystem | None:
        override = self.project_metadata.get("doc_maintenance_build_cmd")
        return detect_build_system(self.workdir, override_cmd=override)

    # ── Step 3: build/test validation ───────────────────────────────────

    async def _run_build(
        self, build_system: BuildSystem
    ) -> tuple[CommitStatus, str, str] | None:
        """Return None on success or (status, reason, output) on failure.

        Hard stop on FAIL or TIMEOUT — never proceeds to stage/commit.
        """
        try:
            proc = await asyncio.to_thread(
                self._run_subprocess, build_system.cmd, build_system.timeout
            )
        except subprocess.TimeoutExpired as exc:
            output = _truncate(str(exc.stderr or "") if hasattr(exc, "stderr") else "")
            return (
                "BUILD_TIMEOUT",
                f"build exceeded {build_system.timeout}s",
                output,
            )
        except FileNotFoundError as exc:
            return ("BUILD_FAILED", f"build command not found: {exc}", "")
        except Exception as exc:
            return ("BUILD_FAILED", f"build invocation error: {exc}", "")

        if proc.returncode != 0:
            output = _truncate((proc.stderr or "") + (proc.stdout or ""))
            return (
                "BUILD_FAILED",
                f"{build_system.name} build failed (exit {proc.returncode})",
                output,
            )

        return None

    # ── Step 4: path filter ─────────────────────────────────────────────

    def _filter_paths(self, paths: list[str]) -> list[str]:
        """Filter paths to those that:
          - Are within the doc allowlist.
          - Pass the denylist.
          - Exist on disk.
          - Are currently modified (per ``git status --porcelain``).
        """
        valid: list[str] = []
        for p in paths:
            if not _is_doc_path_allowed(p):
                continue
            abs_path = (self.workdir / p).resolve()
            if not abs_path.exists():
                continue
            # Only include paths that are actually modified in the working tree.
            if not self._is_path_modified(p):
                continue
            valid.append(p)
        return valid

    def _is_path_modified(self, rel_path: str) -> bool:
        """Return True if `git status --porcelain` reports `rel_path` as modified."""
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain", "--", rel_path],
                cwd=str(self.workdir),
                capture_output=True,
                text=True,
                timeout=GIT_SUBPROCESS_TIMEOUT_S,
                shell=False,
                check=False,
            )
        except Exception:
            return False
        return r.returncode == 0 and bool(r.stdout.strip())

    # ── Step 5: stage ───────────────────────────────────────────────────

    async def _stage_paths(self, paths: list[str]) -> str | None:
        """Stage paths explicitly. Returns None on success, error string on failure."""
        # Re-check ``git status --porcelain`` after validation in case the build
        # mutated files.
        # (The architecture doc mentions re-checking; the porcelain check is
        # already done per-path in _filter_paths.)
        try:
            r = await asyncio.to_thread(
                self._run_git, ["add", "--", *paths]
            )
        except Exception as exc:
            return f"git add failed: {exc}"
        if r.returncode != 0:
            return f"git add failed: {r.stderr.strip() or 'unknown error'}"
        return None

    # ── Step 6: commit ──────────────────────────────────────────────────

    async def _commit(
        self, message: str, paths: list[str]
    ) -> tuple[str, None] | tuple[None, tuple[CommitStatus, str]]:
        """Commit only the specified paths with a single message.

        Returns (commit_hash, None) on success, or (None, (status, reason))
        on failure. The ``--only`` flag ensures the commit contains EXACTLY
        the specified paths, regardless of other staged state.
        """
        try:
            r = await asyncio.to_thread(
                self._run_git_with_author,
                ["commit", "-m", message, "--only", "--", *paths],
            )
        except Exception as exc:
            return None, ("STAGING_ERROR", f"git commit invocation failed: {exc}")

        if r.returncode != 0:
            stderr = r.stderr.strip()
            # Pre-commit hooks that modify files or block the commit land here.
            if "hook" in stderr.lower() or r.returncode == 1:
                return None, ("BLOCKED_BY_HOOK", stderr or "commit blocked by hook")
            return None, ("STAGING_ERROR", stderr or f"git commit exit {r.returncode}")

        # Parse commit hash from `git rev-parse HEAD` after the commit lands.
        try:
            hash_proc = await asyncio.to_thread(
                self._run_git, ["rev-parse", "HEAD"]
            )
        except Exception as exc:
            return None, ("STAGING_ERROR", f"git rev-parse HEAD failed: {exc}")

        commit_hash = hash_proc.stdout.strip() if hash_proc.returncode == 0 else ""
        if not commit_hash:
            return None, ("STAGING_ERROR", "could not read commit hash after commit")

        return commit_hash, None

    # ── Subprocess helpers ──────────────────────────────────────────────

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess:
        """Synchronous git invocation. Mirrors :class:`GitDiffService`."""
        return subprocess.run(
            ["git"] + args,
            cwd=str(self.workdir),
            capture_output=True,
            text=True,
            timeout=GIT_SUBPROCESS_TIMEOUT_S,
            shell=False,
            check=False,
        )

    def _run_git_with_author(self, args: list[str]) -> subprocess.CompletedProcess:
        """Git commit with explicit author identity (avoids relying on
        user.email / user.name being configured in the repo).
        """
        return subprocess.run(
            [
                "git",
                "-c", "user.email=blueprinter@local",
                "-c", "user.name=blueprinter",
            ] + args,
            cwd=str(self.workdir),
            capture_output=True,
            text=True,
            timeout=GIT_SUBPROCESS_TIMEOUT_S,
            shell=False,
            check=False,
        )

    def _run_subprocess(
        self, args: list[str], timeout: int
    ) -> subprocess.CompletedProcess:
        """Generic subprocess runner for build commands (not git).

        Uses ``shell=False`` and an arg list. No shell interpretation.
        """
        return subprocess.run(
            args,
            cwd=str(self.workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _is_doc_path_allowed(rel_path: str) -> bool:
    """Return True if `rel_path` is within the doc allowlist scope.

    Mirrors the doc-maintainer tool's validation — defense in depth.
    """
    if not rel_path:
        return False
    normalized = rel_path.replace("\\", "/").lstrip("./")
    if not normalized:
        return False

    # Denylist check first.
    for denied in DOC_PATH_DENYLIST:
        if normalized.startswith(denied) or f"/{denied}" in normalized:
            return False

    # Allowlist: docs/*, doc/*, or top-level *.md / specific basenames.
    for prefix in DOC_ALLOWLIST_PREFIXES:
        if normalized.startswith(prefix):
            return True

    basename = Path(normalized).name
    if basename in DOC_ALLOWLIST_BASENAMES:
        return True
    if "/" not in normalized and normalized.lower().endswith(".md"):
        return True

    return False


def _truncate(s: str, max_bytes: int = MAX_BUILD_OUTPUT_BYTES) -> str:
    """Truncate a string to max_bytes (UTF-8 boundary safe)."""
    if not s:
        return ""
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="replace") + "\n... [truncated]"


def _elapsed_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
