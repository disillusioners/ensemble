"""File system tools for reading files and directories."""

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from langchain_core.tools import tool

from daemon.services.workspace_guard import WorkspaceGuard
from ._tool_registry import register_tool_category
from ._truncate import truncate_output

# ---------------------------------------------------------------------------
# Walk-tool guardrail constants (2026-09-23 memory-leak incident fix)
# ---------------------------------------------------------------------------
#
# These constants bound the traversal done by glob_files and grep_files.
# Before this fix both tools materialized the entire match set in memory
# with no cap, depth limit, or timeout. A bare absolute search root (e.g.
# /Users/...) widened by an explorer that hadn't found its target pushed
# the daemon past 16 GB RAM in ~3.5 minutes and it was SIGKILLed. The
# bounded walker (_bounded_walk) is the shared enforcement point: glob_files
# and grep_files both go through it.
#
# Module-level constants make these monkey-patchable from the unit tests
# (small timeouts, tiny counts) without rewriting the walker.
#
# Exclusions deliberately OVERLAP with the hidden-dir rule (e.g. .venv and
# .git both start with "."): the explicit list documents intent and covers
# non-hidden noise dirs (node_modules, Library, dist, build, target). Keep
# the two rules so future contributors understand which dirs are pruned
# for what reason. WorkspaceGuard.IGNORE_PATTERNS is a SEPARATE list with
# a SEPARATE purpose (HTTP workspace endpoints) — do not conflate.
WALK_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "node_modules",  # JS deps — typically the largest dir in any JS project
    ".venv", "venv",  # Python virtualenvs
    ".git",  # Git internals (objects, refs, hooks)
    "__pycache__",  # Python bytecode cache
    "Library",  # macOS user Library dir (mitigates ~/ as a search root)
    ".cache",  # Generic cache dirs (pip, npm, etc.)
    ".next",  # Next.js build cache
    "dist",  # JS / Python build output
    "build",  # Various build outputs (setuptools, cmake, …)
    "target",  # Rust / Java build output
})
# Hidden-dir allowlist — dirs that start with "." but are intentionally NOT
# pruned by the hidden-dir rule. ``.agents/`` is this repo's core collaboration
# infra (plans/conventions/memories live there); pruning it silently would
# make grep/glob miss the very files agents most often look for — W-B review
# patch. Add to this set with care; the default is to KEEP hidden dirs hidden.
WALK_ALLOWED_HIDDEN_DIRS: frozenset[str] = frozenset({
    ".agents",
})
WALK_MAX_DEPTH: int = 10  # visit dirs at depths 0..N, scan files at depths 0..N
WALK_MAX_FILE_COUNT: int = 10_000  # bound the materialized candidate list
WALK_MAX_FILE_SIZE_BYTES: int = 1_500_000  # ~1.5 MB — grep_files per-file read cap
WALK_TIMEOUT_SECONDS: float = 20.0  # per-call wall-clock cap (monotonic check)
WALK_MAX_MATCHES_PER_CALL: int = 10_000  # grep_files inner match-accumulation cap

CATEGORY_NAME = "File Operations"
CATEGORY_DOC = """\
Read, write, edit, and search files and directories.

**Rules**:
- `workdir` is required when `path` is relative. If `path` is absolute (e.g. `/abs/path`
  on Unix or `C:\\path\\to\\file` on Windows), `workdir` is REQUIRED for the WALK tools
  (`glob_files`, `grep_files`) — they walk unbounded trees and need an explicit scope.
  For the file tools (`read_file`, `write_file`, `edit_file`) and `list_directory`,
  `workdir` may be omitted for absolute paths and the path is used as-is.
- When `path` is relative, it is resolved against `workdir` and must stay within it.
- WALK tools (`glob_files`, `grep_files`) further require the resolved root to be within
  the workdir OR an allowed temp dir. Absolute roots outside the workdir (e.g. bare
  `/Users/...`) are REFUSED — narrow the root to a directory inside the workdir.
- Both walk tools apply traversal guardrails: excluded dirs (node_modules, .venv,
  .git, __pycache__, Library, .cache, .next, dist, build, target, and any hidden
  dir), depth cap (10 levels), file-count cap (10,000), per-file size cap
  (1.5 MB; grep_files only), and a 20-second wall-clock timeout. When a cap
  fires the result carries a loud "Search incomplete" notice pointing the
  caller to narrow the root or raise pattern specificity.

Example read_file (relative path):
```json
{
  "path": ".agents/shared/planning/<feature>/plan-overview.md",
  "workdir": "/path_to/current/working/project/directory"
}
```

Example read_file (absolute path, workdir not required):
```json
{
  "path": "/tmp/shared/plan-overview.md"
}
```

Example glob_files (absolute path, workdir REQUIRED for walk tools):
```json
{
  "pattern": "**/*.py",
  "path": "/abs/path/to/searchroot",
  "workdir": "/path_to/current/working/project/directory"
}
```

# Backward-compatible wrappers around WorkspaceGuard.
#
# The canonical implementation lives in ``daemon.services.workspace_guard``. These
# thin wrappers preserve the original signatures/error messages so existing
# @tool-decorated functions below and the test suite (``tests/unit/test_filesystem_*``)
# continue to work unchanged.
#
# Walk-tool boundary: ``_resolve_search_root`` (used ONLY by glob_files /
# grep_files) closes the absolute-path bypass that ``_resolve_within_workdir``
# intentionally keeps open for read/write/edit/list_directory. The 2026-09-23
# incident showed that an agent passing a bare ``/Users/...`` as a search
# root caused a 16 GB RAM walk — walk tools now require workdir whenever an
# absolute root is supplied.
"""


def _is_absolute_path(path: str) -> bool:
    """Return True if *path* is absolute on the current OS or matches a Windows
    absolute pattern (drive letter or UNC). Cross-platform safe: a Windows-style
    absolute path is still recognized as absolute when the daemon runs on Unix,
    so agents on either OS get consistent behavior.
    """
    return WorkspaceGuard._is_absolute_path(path)


def _resolve_target_path(
    path: str,
    workdir: str | None,
) -> tuple[Path | None, Path | None, str | None]:
    """Resolve *path* against *workdir* (relative) or use it as-is (absolute).

    Returns:
        (target_path, base_path, error). ``base_path`` is the workdir Path when
        *path* is relative, and ``None`` when *path* is absolute (no boundary check
        is applied). On error, target_path and base_path are None.
    """
    if _is_absolute_path(path):
        try:
            return Path(path).expanduser(), None, None
        except (OSError, RuntimeError) as e:
            return None, None, f"ERROR: Invalid absolute path: {e}"

    if not workdir or not workdir.strip():
        return (
            None,
            None,
            "ERROR: workdir is required for relative paths. Agents must always "
            "specify workdir explicitly — typically the project directory. "
            "Absolute paths do not need workdir.",
        )

    try:
        guard = WorkspaceGuard(workdir)
    except ValueError:
        return None, None, (
            f"ERROR: Working directory does not exist: {workdir} "
            "— check the workdir path. Was it typed correctly?"
        )

    return guard._resolve_target(path)


def _resolve_within_workdir(
    path: str,
    workdir: str | None,
) -> tuple[Path | None, str | None]:
    """Resolve *path* and verify it stays within *workdir* (when relative).

    Combines ``_resolve_target_path`` with the boundary check, so callers get
    a single (target, err) tuple and can't forget to apply the boundary check.

    Returns:
        (target_path, error). On error, target_path is None. For absolute
        paths the boundary check is intentionally skipped.
    """
    if _is_absolute_path(path):
        try:
            return Path(path).expanduser(), None
        except (OSError, RuntimeError) as e:
            return None, f"ERROR: Invalid absolute path: {e}"

    if not workdir or not workdir.strip():
        return (
            None,
            "ERROR: workdir is required for relative paths. Agents must always "
            "specify workdir explicitly — typically the project directory. "
            "Absolute paths do not need workdir.",
        )

    try:
        guard = WorkspaceGuard(workdir)
    except ValueError:
        return None, (
            f"ERROR: Working directory does not exist: {workdir} "
            "— check the workdir path. Was it typed correctly?"
        )

    return guard.resolve(path)


def _normed_contains(base: Path, target: Path) -> bool:
    """Check if target is within base using OS-appropriate case normalization."""
    return WorkspaceGuard._normed_contains(base, target)


def _is_within_workdir(workdir: Path, target: Path) -> bool:
    """Check if target path is within workdir boundary or a temp directory.

    Paths are allowed if they are:
    1. Within the workdir, OR
    2. Within the system temp directory or common temp directories
    """
    if WorkspaceGuard._normed_contains(workdir, target):
        return True
    return WorkspaceGuard._is_in_temp_dir(target)


def _resolve_search_root(
    path: str,
    workdir: str | None,
) -> tuple[Path | None, str | None]:
    """Resolve a search root for WALK tools (glob_files, grep_files) ONLY.

    Closes the absolute-path bypass that ``_resolve_within_workdir`` keeps open
    for read/write/edit/list_directory. Without this, an agent passing a bare
    absolute path (e.g. ``/Users/...``) makes the walk unbounded — the
    2026-09-23 incident root cause (16 GB RAM walk → SIGKILL).

    Semantics:
      - Relative path: delegates to ``_resolve_within_workdir`` (same workdir
        requirement and behavior as the file tools — relative path stays
        inside the workdir or temp dir).
      - Absolute path + workdir: allowed iff the resolved root is within the
        workdir (normed-contains, symlink-resolved via ``WorkspaceGuard``)
        OR within an allowed temp dir. Otherwise REFUSED with a redirecting
        error.
      - Absolute path + no workdir: allowed ONLY if the resolved root is
        within an allowed temp dir (matches ``WorkspaceGuard``'s existing
        "trusted by design" temp-dir allowance — see
        ``WorkspaceGuard._is_in_temp_dir``). Otherwise REFUSED — without
        workdir and outside temp, the walk has no declared scope and can
        grow to the whole filesystem (the incident shape).

    Returns:
        (target_path, error). On error, target_path is None.
    """
    if not _is_absolute_path(path):
        # Relative paths keep the existing _resolve_within_workdir semantics.
        return _resolve_within_workdir(path, workdir)

    # Absolute path: resolve the target first, then check the boundary.
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as e:
        return None, f"ERROR: Invalid absolute path: {e}"

    # Path inside any allowed temp dir: allowed unconditionally (matches
    # WorkspaceGuard's "trusted by design" temp-dir allowance). Both with
    # and without workdir — temp roots are considered safe by design.
    if WorkspaceGuard._is_in_temp_dir(target):
        return target, None

    # Path outside temp + no workdir: REFUSE. This is the incident shape
    # — a bare absolute path like /Users/... with no workdir declaration.
    if not workdir or not workdir.strip():
        return None, (
            "ERROR: glob_files and grep_files require an explicit workdir when "
            "using an absolute search root that is not within an allowed temp "
            "directory. Bare absolute paths (e.g. /Users/...) can trigger "
            "runaway memory walks — this is the 2026-09-23 incident root "
            "cause. Pass workdir=<project_root> to scope the walk, or use "
            "a path under /tmp, /var/tmp, or the system temp dir. "
            f"Got path={path!r}, workdir={workdir!r}."
        )

    # Path outside temp + workdir provided: check workdir containment.
    try:
        guard = WorkspaceGuard(workdir)
    except ValueError:
        return None, (
            f"ERROR: Working directory does not exist: {workdir} "
            "— check the workdir path. Was it typed correctly?"
        )

    if not guard.is_within(target):
        return None, (
            f"ERROR: Search root outside workspace boundary: {path}. "
            f"glob_files / grep_files walks are bounded to the workdir "
            f"({workdir}) or allowed temp dirs — narrow the root to a "
            f"directory inside the workdir."
        )

    return target, None


@dataclass
class WalkReport:
    """Outcome of a bounded tree walk — drives both glob_files and grep_files.

    Attributes:
        files: All files (regular files only) found in the bounded walk.
            Excludes anything under directories in ``WALK_EXCLUDED_DIRS`` or
            hidden directories (name starts with ``.``) — except dirs in
            ``WALK_ALLOWED_HIDDEN_DIRS`` (``{".agents"}``), which stay
            traversable. Bounded by ``WALK_MAX_FILE_COUNT``,
            ``WALK_MAX_DEPTH``, and ``WALK_TIMEOUT_SECONDS`` — once any of
            those caps fires, the walk stops and the reason is recorded in
            ``stopped_reason``.
        stopped_reason: ``None`` if the walk completed normally; one of
            ``"file_count"`` / ``"depth"`` / ``"timeout"`` if a cap fired.
            Drives the "Search incomplete" notice appended to tool results.
        timed_out_at: Path where the timeout fired (``None`` if no timeout).
        deadline_monotonic: ``time.monotonic()`` deadline the walker enforced
            (start + ``WALK_TIMEOUT_SECONDS``). Exposed so post-walk phases
            (grep read loop, glob stat loop) can reuse the SAME budget
            instead of getting a fresh 20 s — prevents a slow walk + slow
            read phase from stalling the worker pool. ``0.0`` sentinel when
            the walker did not run (e.g. caller did not invoke it).
        post_walk_phase_timed_out: ``None`` if the post-walk phase (grep read
            or glob mtime-stat) completed within budget; ``"read"`` /
            ``"stat"`` to flag which phase exceeded the deadline. Drives a
            second clause of the truncation notice — W-A review patch.
    """

    files: list[Path] = field(default_factory=list)
    stopped_reason: str | None = None
    timed_out_at: Path | None = None
    deadline_monotonic: float = 0.0
    post_walk_phase_timed_out: str | None = None


def _bounded_walk(
    search_path: Path,
    *,
    max_depth: int | None = None,
    max_file_count: int | None = None,
    timeout_seconds: float | None = None,
) -> WalkReport:
    """Walk ``search_path`` with exclusions, depth cap, count cap, and timeout.

    Replaces the naive ``Path.glob`` and ``search_path.glob('**/*')`` walks
    for the walk tools. Pathlib's glob materializes the FULL match list with
    no pruning hook — that's how the 2026-09-23 incident's 16 GB walk
    happened. ``os.walk(topdown=True)`` lets us prune ``dirs[:]`` in-place to
    honor exclusions and depth cap, AND stop the walk the moment a cap fires
    (don't keep walking to discard later).

    Symlinks: ``followlinks=False`` (default) — we do NOT follow symlinks. A
    symlinked directory appears in the listing but its contents are not
    iterated. This avoids symlink cycles and accidental escapes.

    Hidden files: NOT excluded (matches ``Path.glob`` semantics — both
    visible and hidden files match ``*.py``). Hidden DIRECTORIES are pruned
    (per requirement).

    Args:
        search_path: Absolute path to walk.
        max_depth: Visit dirs at depths ``0..max_depth`` (files at depths
            ``0..max_depth``). Default: ``WALK_MAX_DEPTH`` (looked up at
            CALL TIME via ``globals()`` so monkeypatch.setattr works in tests).
        max_file_count: Hard limit on the candidate list size. The walk
            STOPS the moment this is hit (stop-the-walk, not post-filter).
            Default: ``WALK_MAX_FILE_COUNT`` (looked up at call time).
        timeout_seconds: Per-call wall-clock budget via ``time.monotonic()``
            checks at the start of each directory. Default:
            ``WALK_TIMEOUT_SECONDS`` (looked up at call time).

    Returns:
        ``WalkReport`` with all matching files and ``stopped_reason`` set if
        any cap fired.
    """
    # Look up module-level constants at CALL TIME (not at function-definition
    # time) so monkeypatch.setattr in unit tests can override them. Defaults
    # are ``None`` to distinguish "use the current module value" from an
    # explicit caller override.
    if max_depth is None:
        max_depth = globals()["WALK_MAX_DEPTH"]
    if max_file_count is None:
        max_file_count = globals()["WALK_MAX_FILE_COUNT"]
    if timeout_seconds is None:
        timeout_seconds = globals()["WALK_TIMEOUT_SECONDS"]
    # WALK_EXCLUDED_DIRS is read from globals() too (consistent semantics —
    # tests can monkeypatch the exclusion set if they need a custom policy).
    excluded = globals()["WALK_EXCLUDED_DIRS"]

    files: list[Path] = []
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    timed_out_at: Path | None = None
    saw_depth_cap = False

    for dirpath, dirs, fnames in os.walk(
        str(search_path), topdown=True, followlinks=False,
    ):
        # Timeout check (cheap — done once per directory entry).
        if time.monotonic() > deadline:
            timed_out = True
            timed_out_at = Path(dirpath)
            break

        current = Path(dirpath)

        # Depth computation (relative to search_path).
        try:
            rel = current.relative_to(search_path)
            depth = len(rel.parts)
        except ValueError:
            depth = 0

        # Prune excluded / hidden dirs (in-place for topdown=True).
        # Hidden-dir allowlist (WALK_ALLOWED_HIDDEN_DIRS) takes precedence
        # over the startswith(".") rule — e.g. ".agents/" is allowlisted
        # because plans/conventions live there and must stay searchable.
        # Sorted for deterministic walk order — helps test reproducibility.
        allowed_hidden = globals()["WALK_ALLOWED_HIDDEN_DIRS"]
        kept_dirs = sorted(
            d for d in dirs
            if d not in excluded
            and (not d.startswith(".") or d in allowed_hidden)
        )
        dirs[:] = kept_dirs

        # Depth cap: at max_depth we don't descend further (files at this
        # depth are still scanned — only descent stops). Only flag the cap
        # when there were actually dirs to prune; a tree that GENUINELY ends
        # at the cap (dirs already empty) is not "incomplete" and would
        # otherwise emit a false truncation notice — W-F review patch.
        if depth >= max_depth:
            if dirs:
                saw_depth_cap = True
            dirs[:] = []

        # Add files (don't follow symlinks — already enforced by followlinks=False).
        for fname in fnames:
            full = current / fname
            # is_file() filters out symlinks-to-directories, sockets, etc.
            if not full.is_file():
                continue
            files.append(full)
            # Stop-the-walk on count cap (don't keep walking to discard later).
            if len(files) >= max_file_count:
                return WalkReport(
                    files=files,
                    stopped_reason="file_count",
                    timed_out_at=None,
                    deadline_monotonic=deadline,
                )

    # Determine final stopped_reason. Timeout wins over depth if both fired.
    stopped_reason: str | None = None
    if timed_out:
        stopped_reason = "timeout"
    elif saw_depth_cap:
        stopped_reason = "depth"

    return WalkReport(
        files=files,
        stopped_reason=stopped_reason,
        timed_out_at=timed_out_at,
        deadline_monotonic=deadline,
    )


def _file_matches_pattern(
    file_path: Path,
    search_path: Path,
    pattern: str,
) -> bool:
    """Check if *file_path* (relative to *search_path*) matches the glob pattern.

    Uses ``PurePath.full_match`` (Python 3.13+) which reproduces pathlib glob
    semantics including ``**`` for recursive matching. The pattern is matched
    against the path RELATIVE to ``search_path`` — the same way
    ``search_path.glob(pattern)`` interprets it.

    Brace expansion is the CALLER's responsibility (see
    ``_expand_single_level_braces``); this function treats braces literally,
    matching ``Path.glob``'s native behaviour.

    Examples (search_path = /tmp/test):
        /tmp/test/foo.py            pattern="*.py"        → rel="foo.py" → True
        /tmp/test/nested/foo.py     pattern="*.py"        → rel="nested/foo.py"  → False (* doesn't cross /)
        /tmp/test/nested/foo.py     pattern="**/*.py"     → rel="nested/foo.py"  → True
        /tmp/test/src/sub/foo.py    pattern="src/**/*.py" → rel="src/sub/foo.py" → True
        /tmp/test/other/foo.py      pattern="src/**/*.py" → rel="other/foo.py"   → False (anchored)
    """
    try:
        rel = file_path.relative_to(search_path)
    except ValueError:
        return False
    return PurePath(str(rel)).full_match(pattern)


def _walk_truncation_notice(
    report: WalkReport,
    oversized_skips: int = 0,
    match_capped: bool = False,
) -> str:
    """Build the loud "Search incomplete" notice — only when a cap fired.

    Exclusions are ALWAYS on (not an incomplete signal). The notice fires
    ONLY when a cap (file count / depth / timeout / post-walk phase) or a
    size skip actually tripped during the current call. Happy-path result
    format is unchanged (notice is the empty string when nothing tripped).

    The notice redirects the caller to narrow the root or raise pattern
    specificity — the 2026-09-23 incident's behavioural driver was an agent
    WIDENING roots when searches found nothing; silent truncation would
    make that worse.

    Module-level constants are read via ``globals()`` so monkeypatch.setattr
    on the caller's module attribute surfaces in the notice text (tests
    with tiny caps pin the exact wording).
    """
    g = globals()
    max_file_count = g["WALK_MAX_FILE_COUNT"]
    max_depth = g["WALK_MAX_DEPTH"]
    timeout_seconds = g["WALK_TIMEOUT_SECONDS"]
    max_file_size_bytes = g["WALK_MAX_FILE_SIZE_BYTES"]
    max_matches = g["WALK_MAX_MATCHES_PER_CALL"]

    reasons: list[str] = []
    if report.stopped_reason == "file_count":
        reasons.append(f"file-count cap reached ({max_file_count:,} files)")
    elif report.stopped_reason == "depth":
        reasons.append(f"depth cap reached ({max_depth} levels)")
    elif report.stopped_reason == "timeout":
        reasons.append(f"timeout ({timeout_seconds:g}s)")
    if report.post_walk_phase_timed_out == "read":
        # Grep read phase (file read_text + line scan) ran past the
        # walker's deadline. Reported as a separate reason so the caller
        # can tell the walk itself was fine but the read loop was the
        # bottleneck — W-A review patch.
        reasons.append("timeout during read phase")
    elif report.post_walk_phase_timed_out == "stat":
        # Glob post-walk mtime-stat phase ran past the walker's deadline.
        # Same W-A intent: stat() per file is the bottleneck.
        reasons.append("timeout during stat phase")
    if oversized_skips > 0:
        size_mb = max_file_size_bytes / 1_048_576
        reasons.append(
            f"{oversized_skips} oversized file(s) skipped (>{size_mb:.1f}MB)"
        )
    if match_capped:
        reasons.append(
            f"match-count cap reached ({max_matches:,} matches)"
        )

    if not reasons:
        return ""

    return (
        "\n⚠ Search incomplete: " + "; ".join(reasons) + ". "
        "Results may be incomplete — narrow the root or raise pattern specificity."
    )


@register_tool_category("filesystem")
@tool
def list_directory(
    path: str,
    workdir: str | None = None,
    show_hidden: bool = False
) -> str:
    """List directory contents. Use tool_help("list_directory") for details."""
    dir_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        if not dir_path.exists():
            return f"ERROR: Path does not exist: {path}"
        
        if not dir_path.is_dir():
            return f"ERROR: Not a directory: {path}"
        
        entries = []
        for entry in sorted(dir_path.iterdir()):
            name = entry.name
            
            # Skip hidden files unless requested
            if not show_hidden and name.startswith("."):
                continue
            
            # Add type indicator
            if entry.is_dir():
                name += "/"
            elif entry.is_symlink():
                name += "@"
            elif os.access(entry, os.X_OK):
                name += "*"
            
            entries.append(name)
        
        if not entries:
            return f"(empty directory: {dir_path})"
        
        # Apply truncation for safety
        content = "\n".join(entries)
        result = truncate_output(
            content,
            tool_name="list_directory",
            max_chars=6000,
            max_lines=150,
            offset_indexed=False,  # 1-indexed offset (for consistency with other tools)
        )
        
        if result.truncated:
            return result.content + "\n💡 **Tip:** Use more specific paths (e.g., `path=\"subdir\"`) to narrow the listing."
        return content
        
    except Exception as e:
        return f"ERROR: {str(e)}"

list_directory._full_doc_ = """List contents of a directory.

Args:
    path: Directory path to list. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`.
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    show_hidden: Whether to show hidden files (default: False)

Returns:
    Directory listing with file type indicators:
    - / suffix for directories
    - @ suffix for symlinks
    - * suffix for executables
"""

@register_tool_category("filesystem")
@tool
def read_file(
    path: str,
    workdir: str | None = None,
    offset: int = 1,
    limit: int = 2000,
) -> str:
    """Read file contents. Use tool_help("read_file") for details."""
    file_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        if not file_path.exists():
            return f"ERROR: File does not exist: {path}"
        
        if not file_path.is_file():
            return f"ERROR: Not a file: {path}"
        
        lines = file_path.read_text(encoding="utf-8").splitlines()
        
        # Apply offset and limit (1-indexed offset)
        start = max(0, offset - 1)
        end = start + limit
        selected_lines = lines[start:end]
        
        # Format with line numbers
        result = []
        for i, line in enumerate(selected_lines, start=offset):
            # Truncate very long lines
            if len(line) > 2000:
                line = line[:2000] + "... (truncated)"
            result.append(f"{i}: {line}")
        
        total_lines = len(lines)
        header = f"File: {file_path} ({total_lines} lines total)\n{'-' * 40}\n"
        formatted_content = header + "\n".join(result)
        
        # Check if truncation needed (character limit for safety)
        if len(formatted_content) > 6000:
            # Find a good truncation point at line boundary
            truncated_lines = []
            char_count = 0
            for line in result:
                if char_count + len(line) + 1 > 6000:
                    break
                truncated_lines.append(line)
                char_count += len(line) + 1
            
            shown_lines = len(truncated_lines)
            end_line = offset + shown_lines - 1
            next_offset = offset + shown_lines
            
            # Build pagination hint
            pagination_hint = (
                f"\n---\n"
                f"Showing lines {offset} to {end_line} of {total_lines}. "
                f"Use offset={next_offset} for more."
            )
            
            truncated_content = header + "\n".join(truncated_lines)
            return truncated_content + pagination_hint
        
        # Add hint when content fits char limit but lines exceed limit
        end_line = offset + len(selected_lines) - 1
        if total_lines > end_line:
            return formatted_content + f"\n\n---\nShowing lines {offset} to {end_line} of {total_lines}. Use offset={end_line + 1} for more."
        
        return formatted_content
        
    except UnicodeDecodeError:
        return f"ERROR: Cannot read file as text (binary file?): {path}"
    except Exception as e:
        return f"ERROR: {str(e)}"

read_file._full_doc_ = """Read contents of a file.

Args:
    path: File path to read. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`.
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    offset: Line number to start from (1-indexed, default: 1)
    limit: Maximum number of lines to read (default: 2000)

Returns:
    File contents with line numbers (format: "line_num: content")
"""


@register_tool_category("filesystem")
@tool
def glob_files(
    pattern: str,
    workdir: str | None = None,
    path: str = ".",
    offset: int = 0,
    limit: int = 100,
) -> str:
    """Find files matching a glob pattern. Use tool_help("glob_files") for details."""
    # Walk-tool absolute-path guard (2026-09-23 incident fix). Bare absolute
    # roots outside the workdir are REFUSED — see _resolve_search_root.
    search_path, err = _resolve_search_root(path, workdir)
    if err:
        return err

    try:
        if not search_path.exists():
            return f"ERROR: Path does not exist: {path}"

        # Bounded walk — applies exclusions / depth / count / timeout caps.
        walk = _bounded_walk(search_path)
        candidate_files = walk.files

        # Filter candidates by the user's pattern (uses _file_matches_pattern
        # internally; preserves Path.glob semantics including `**`).
        matches = [f for f in candidate_files if _file_matches_pattern(f, search_path, pattern)]
        # is_file() is already enforced by the walker, but a defensive double-
        # check covers the rare case of a file disappearing mid-walk.
        files = [m for m in matches if m.is_file()]

        if not files:
            truncation_notice = _walk_truncation_notice(walk)
            if truncation_notice:
                return f"No files matching pattern: {pattern}{truncation_notice}"
            return f"No files matching pattern: {pattern}"

        # Sort by modification time (newest first). One stat() per file; with
        # the walker's WALK_MAX_FILE_COUNT cap the syscall budget is bounded.
        # BUT a single slow stat() on a network FS or a contention-stalled
        # disk can stall the worker — check the walker's deadline before
        # each stat() so a no-match glob cannot hold a worker for minutes
        # past WALK_TIMEOUT_SECONDS — W-A review patch.
        files_with_mtime: list[tuple[float, Path]] = []
        stat_deadline = walk.deadline_monotonic or (
            time.monotonic() + globals()["WALK_TIMEOUT_SECONDS"]
        )
        for f in files:
            if time.monotonic() > stat_deadline:
                walk.post_walk_phase_timed_out = "stat"
                break
            try:
                mtime = f.stat().st_mtime
            except OSError:
                # File disappeared between walk and stat — skip it.
                continue
            files_with_mtime.append((mtime, f))
        files_with_mtime.sort(key=lambda x: x[0], reverse=True)
        files = [f for _, f in files_with_mtime]

        # Format output relative to search_path
        result = []
        for f in files:
            try:
                rel_path = f.relative_to(search_path)
                result.append(str(rel_path))
            except ValueError:
                result.append(str(f))

        # Apply pagination
        if offset > 0:
            result = result[offset:]
        if limit and limit > 0:
            result = result[:limit]

        truncation_notice = _walk_truncation_notice(walk)

        if not result:
            if truncation_notice:
                return f"No files matching pattern: {pattern}{truncation_notice}"
            return f"No files matching pattern: {pattern}"

        content = "\n".join(result)

        # Check if truncation needed
        if len(content) > 6000 or len(result) > limit:
            # Truncate at line boundary
            truncated_lines = result[:limit]
            shown = len(truncated_lines)
            total = len(files)
            next_offset = offset + limit

            # Build pagination hint
            pagination_hint = (
                f"\n---\n"
                f"Showing results {offset + 1} to {offset + shown} of {total}. "
                f"Use offset={next_offset} for next page."
            )

            return "\n".join(truncated_lines) + pagination_hint + truncation_notice

        return content + truncation_notice

    except Exception as e:
        return f"ERROR: {str(e)}"

glob_files._full_doc_ = """Find files matching a glob pattern.

Args:
    pattern: Glob pattern (e.g., "**/*.py", "*.md", "src/**/*.ts")
    workdir: Base directory for relative paths. REQUIRED when `path` is
              absolute (walk-tool boundary — 2026-09-23 incident fix);
              required as usual when `path` is relative.
    path: Directory to search in. Absolute paths are accepted only when
          workdir is provided AND the resolved root is within the workdir
          or an allowed temp dir. Relative paths are resolved against
          `workdir`. Default: "."
    offset: Number of results to skip (default: 0)
    limit: Maximum results to return (default: 100)

Traversal guardrails: excluded dirs (node_modules, .venv, venv, .git,
__pycache__, Library, .cache, .next, dist, build, target, and any hidden
dir), depth cap (10 levels), file-count cap (10,000), and a 20-second
wall-clock timeout. When a cap fires the result carries a loud "Search
incomplete" notice pointing the caller to narrow the root or raise
pattern specificity.

Returns:
    List of matching file paths, sorted by modification time (newest first).
    A trailing "Search incomplete" notice is appended only when a cap fired.
"""


@register_tool_category("filesystem")
@tool
def write_file(
    content: str,
    path: str,
    workdir: str | None = None,
    append: bool = False
) -> str:
    """Write or append content to a file. Use tool_help("write_file") for details."""
    file_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        mode = "a" if append else "w"
        with open(file_path, mode, encoding="utf-8") as f:
            f.write(content)
        
        action = "Appended to" if append else "Written to"
        return f"SUCCESS: {action} {file_path}"
        
    except Exception as e:
        return f"ERROR: {str(e)}"

write_file._full_doc_ = """Write or append content to a file.

Args:
    content: The text content to write
    path: File path to write to. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`.
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    append: If True, append to existing file; if False, overwrite (default: False)

Returns:
    Success message with the file path
"""


def _expand_single_level_braces(pattern: str) -> list[str]:
    """Expand a single-level ``{a,b,c}`` group into one pattern per alternate.

    Only the first brace group is considered. Nested braces — a ``{`` found
    between the outermost open and close — are NOT expanded; the original
    pattern is returned unchanged so the caller can decide what to do with it
    (in practice ``Path.glob`` treats braces as literal characters).
    """
    open_idx = pattern.find("{")
    if open_idx == -1:
        return [pattern]
    close_idx = pattern.find("}", open_idx + 1)
    if close_idx == -1:
        return [pattern]
    # Nested-brace detection: another '{' between the outer pair means we
    # leave the pattern literal — nested expansion is out of scope.
    inner_open_idx = pattern.find("{", open_idx + 1)
    if inner_open_idx != -1 and inner_open_idx < close_idx:
        return [pattern]
    head = pattern[:open_idx]
    tail = pattern[close_idx + 1:]
    alts = pattern[open_idx + 1:close_idx].split(",")
    return [head + alt + tail for alt in alts]


def _filter_candidates_by_include(
    search_path: Path,
    include: str,
    candidate_files: list[Path],
) -> list[Path]:
    """Apply the user-supplied include filter to the bounded candidate list.

    Pure filter — does NOT walk the tree (that's ``_bounded_walk``'s job).
    Separating the walk from the pattern filter lets callers (notably
    ``grep_files``) do ONE walk and reuse the WalkReport for both filtering
    AND the truncation notice — otherwise the walk runs twice per call.

    Behavior (preserved from the original ``_expand_include_glob``):
    - Empty include returns the candidate list unchanged (the walker
      already bounded it). Sorted for deterministic output.
    - When ``include`` contains no ``**`` segment, ``**/`` is prepended so
      the filter is recursive at any depth under ``search_path``.
    - Patterns already containing ``**`` are used VERBATIM.
    - Single-level brace alternates (``{a,b,c}``) are expanded; each is
      matched against the candidate list and the union is deduped.
    - Nested braces (``{a,{b,c}}``) are NOT expanded; the pattern is matched
      literally against each candidate (matches ``Path.glob``'s native
      treatment of braces).
    """
    pattern = include if include else ""
    if not pattern:
        # No-include path: every candidate passes (walker already bounded).
        # Sorted for deterministic output — same observable property as the
        # pre-fix baseline.
        return sorted(candidate_files, key=str)

    # Recursion: prepend "**/" when the pattern does not already recurse.
    # ORDER MATTERS: the `**`-check runs BEFORE brace expansion. This keeps
    # the `**/` prefix intact in EVERY alternate — e.g. `*.{ts,html}` →
    # `**/*.{ts,html}` then expands to `**/*.ts` and `**/*.html`. A naive
    # refactor that expanded braces first and then unconditionally
    # prepended `**/` to each result would produce `**/**.ts` /
    # `**/**.html` for `**/*.{ts,html}` — a double-`/` glob pathlib treats
    # as literal `**` segments and matches nothing. Keep the prepend on
    # the WHOLE pre-expansion pattern (the prefix lives in `head`, the
    # brace group sits in the suffix).
    if "**" not in pattern:
        pattern = "**/" + pattern

    # Single-level brace expansion (nested braces fall through verbatim).
    globs = _expand_single_level_braces(pattern)

    seen: set[str] = set()
    results: list[Path] = []
    for g in globs:
        for f in candidate_files:
            if not _file_matches_pattern(f, search_path, g):
                continue
            key = str(f)
            if key in seen:
                continue
            seen.add(key)
            results.append(f)

    # Deterministic ordering.
    results.sort(key=str)
    return results


def _expand_include_glob(search_path: Path, include: str) -> list[Path]:
    """Return the deduped, deterministically-ordered list of FILES under
    ``search_path`` matched by ``include``.

    Refactored (2026-09-23 memory-leak fix): the tree traversal itself is now
    done by ``_bounded_walk`` (which applies the exclusions / depth / count /
    timeout guardrails), then ``_filter_candidates_by_include`` applies the
    user-supplied include filter on top of the bounded candidate list.

    External callers see the same 2-arg signature and the same observable
    semantics; the pin suite at
    ``tests/unit/test_filesystem_grep_include_recursive_regression.py`` stays
    green. Tool callers that need the WalkReport (e.g. ``grep_files``) should
    call ``_bounded_walk`` directly and then ``_filter_candidates_by_include``
    — that way the walk only runs once.
    """
    # Bounded walk — applies exclusions / depth / count / timeout caps.
    walk = _bounded_walk(search_path)
    return _filter_candidates_by_include(search_path, include, walk.files)


@register_tool_category("filesystem")
@tool
def grep_files(
    pattern: str,
    workdir: str | None = None,
    path: str = ".",
    include: str = "",
    case_sensitive: bool = False,
    whole_word: bool = False,
    offset: int = 0,
    limit: int = 100,
) -> str:
    """Search file contents using regex patterns. Use tool_help("grep_files") for details."""
    # Walk-tool absolute-path guard (2026-09-23 incident fix). Bare absolute
    # roots outside the workdir are REFUSED — see _resolve_search_root.
    search_path, err = _resolve_search_root(path, workdir)
    if err:
        return err

    try:
        if not search_path.exists():
            return f"ERROR: Path does not exist: {path}"

        # Compile regex
        flags = 0 if case_sensitive else re.IGNORECASE
        if whole_word:
            pattern = rf"\b{re.escape(pattern)}\b"

        regex = re.compile(pattern, flags)

        # Bounded walk — applies exclusions / depth / count / timeout caps.
        # Single walk reused for both the filtered candidate list AND the
        # truncation notice (WalkReport.stoppped_reason). Walking twice would
        # double the syscall budget on pathological trees.
        walk = _bounded_walk(search_path)

        # Resolve include filter on top of the bounded walk's output
        # (recursive + single-level brace expansion).
        candidate_files = _filter_candidates_by_include(search_path, include, walk.files)

        # Stamp ONE phase deadline (shared by stat + read phases below).
        # Reuses the walker's stamped deadline so a slow walk + slow post-walk
        # phases cannot combine into a worker stall past WALK_TIMEOUT_SECONDS
        # — N1 review patch. glob_files does the same for its mtime-stat loop.
        phase_deadline = walk.deadline_monotonic or (
            time.monotonic() + globals()["WALK_TIMEOUT_SECONDS"]
        )

        # Per-file size cap (the 2026-09-23 incident showed that read_text()
        # on a multi-GB file is itself a crash vector). Stat-then-skip — we
        # never even open files above the cap. Read at call time so
        # monkeypatch.setattr works in unit tests. Deadline also enforced
        # per-candidate so a slow stat() (network FS, contention-stalled disk)
        # cannot stall the worker past WALK_TIMEOUT_SECONDS — closes the N1
        # gap left over after W-A's walk-layer + read-layer fixes.
        max_file_size_bytes = globals()["WALK_MAX_FILE_SIZE_BYTES"]
        max_matches_per_call = globals()["WALK_MAX_MATCHES_PER_CALL"]
        oversized_skips = 0
        readable_files: list[Path] = []
        for f in candidate_files:
            if time.monotonic() > phase_deadline:
                walk.post_walk_phase_timed_out = "stat"
                break
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size > max_file_size_bytes:
                oversized_skips += 1
                continue
            readable_files.append(f)

        # Inner match-accumulation cap — even with the file-count cap, each
        # candidate file can yield many lines. Stop reading once we hit the
        # cap; this prevents one giant file from blowing past the worker's
        # caps. The user's `limit` then trims to their preferred pagination.
        # Deadline also enforced per-file so a slow read_text / line scan
        # cannot stall the worker past WALK_TIMEOUT_SECONDS — W-A review patch.
        # Shares the same phase_deadline as the stat loop above; the None-guard
        # prevents the read-phase check from overwriting a "stat" reason
        # stamped by the size-prefilter loop — N1 review patch.
        match_capped = False
        matches: list[str] = []
        read_budget = max_matches_per_call

        for file_path in readable_files:
            if (
                walk.post_walk_phase_timed_out is None
                and time.monotonic() > phase_deadline
            ):
                walk.post_walk_phase_timed_out = "read"
                break
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, PermissionError, IsADirectoryError):
                continue

            for line_num, line in enumerate(lines, start=1):
                if regex.search(line):
                    # Truncate long lines
                    display_line = line[:500] + "..." if len(line) > 500 else line
                    matches.append(f"{file_path}:{line_num}: {display_line}")
                    if len(matches) >= read_budget:
                        match_capped = True
                        break
            if match_capped:
                break

        # Apply pagination
        if offset > 0:
            matches = matches[offset:]
        if limit and limit > 0:
            matches = matches[:limit]

        # Truncation notice combines walker-side caps (depth / file_count /
        # timeout) with grep-specific signals (oversized skips, match cap).
        truncation_notice = _walk_truncation_notice(
            walk,
            oversized_skips=oversized_skips,
            match_capped=match_capped,
        )

        if not matches:
            if truncation_notice:
                return f"No matches found for: {pattern}{truncation_notice}"
            return f"No matches found for: {pattern}"

        content = "\n".join(matches)

        # Check if truncation needed
        if len(content) > 6000 or len(matches) > limit:
            # Truncate at line boundary
            truncated_matches = matches[:limit]
            shown = len(truncated_matches)
            total = len(matches)
            next_offset = offset + limit

            # Build pagination hint
            pagination_hint = (
                f"\n---\n"
                f"Showing results {offset + 1} to {offset + shown} of {total}. "
                f"Use offset={next_offset} for next page."
            )

            return "\n".join(truncated_matches) + pagination_hint + truncation_notice

        return content + truncation_notice

    except re.error as e:
        return f"ERROR: Invalid regex pattern: {e}"
    except Exception as e:
        return f"ERROR: {str(e)}"

grep_files._full_doc_ = """Search file contents using regex patterns.

Args:
    pattern: Regex pattern to search for
    workdir: Base directory for relative paths. REQUIRED when `path` is
              absolute (walk-tool boundary — 2026-09-23 incident fix);
              required as usual when `path` is relative.
    path: Directory to search in. Absolute paths are accepted only when
          workdir is provided AND the resolved root is within the workdir
          or an allowed temp dir. Relative paths are resolved against
          `workdir`. Default: "."
    include: Glob pattern to filter files. Recursive by default — prefix-less
              patterns (e.g. "*.py", "*.{py,html}") match at ANY depth under
              `path` (the tool prepends `**/`). Path-prefixed patterns (e.g.
              "src/*.ts") are ANCHORED to a single `src/` directory and only
              match DIRECT children — use "src/**/*.ts" for deep matching
              under `src/`. Brace semantics: only the FIRST top-level
              `{a,b,c}` group is expanded (subsequent groups are treated
              literally — `*.{ts,html}.{bak,tmp}` only expands `ts`/`html`,
              the `.{bak,tmp}` suffix is left as-is). Empty alternates are
              harmless (`{a,}` works). Mixed-brace consequence: when the
              WHOLE pattern contains `**`, ALL alternates are used verbatim
              with no per-alternate `**/` prepend — so prefer
              `**/*.{ts,html}` (recursive over both extensions) over
              `{**/*.ts,*.html}` (the `*.html` alternate only matches
              root-level). Patterns already containing "**" (e.g. "**/*.ts",
              "src/**/*.py") are passed through verbatim. Examples: "*.py",
              "**/*.ts", "*.{js,ts}", "src/**/*.html"
    case_sensitive: Whether search is case-sensitive (default: False)
    whole_word: Match whole words only (default: False)
    offset: Number of results to skip (default: 0)
    limit: Maximum matches to return (default: 100)

Traversal guardrails: excluded dirs (node_modules, .venv, venv, .git,
__pycache__, Library, .cache, .next, dist, build, target, and any hidden
dir), depth cap (10 levels), file-count cap (10,000), per-file size cap
(1.5 MB; oversized files are skipped silently — counted in the notice),
match-accumulation cap (10,000), and a 20-second wall-clock timeout. When
a cap fires the result carries a loud "Search incomplete" notice pointing
the caller to narrow the root or raise pattern specificity.

Returns:
    Matching lines with file path and line number (format: "path:line: content").
    A trailing "Search incomplete" notice is appended only when a cap fired.
"""


@register_tool_category("filesystem")
@tool
def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    workdir: str | None = None,
    replace_all: bool = False
) -> str:
    """Replace text in a file using exact string matching. Use tool_help("edit_file") for details."""
    file_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        if not file_path.exists():
            return f"ERROR: File does not exist: {path}"
        
        if not file_path.is_file():
            return f"ERROR: Not a file: {path}"
        
        content = file_path.read_text(encoding="utf-8")
        
        if old_string not in content:
            return f"ERROR: String not found in file: {old_string[:100]}"
        
        count = content.count(old_string)
        
        if replace_all:
            new_content = content.replace(old_string, new_string)
            action = f"Replaced all {count} occurrences"
        else:
            if count > 1:
                return f"ERROR: String found {count} times. Use replace_all=True to replace all occurrences."
            new_content = content.replace(old_string, new_string, 1)
            action = "Replaced 1 occurrence"
        
        file_path.write_text(new_content, encoding="utf-8")
        
        return f"SUCCESS: {action} in {file_path}"
        
    except Exception as e:
        return f"ERROR: {str(e)}"

edit_file._full_doc_ = """Replace text in a file using exact string matching.

Args:
    path: File path to edit. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`.
    old_string: The exact string to find and replace (supports multi-line)
    new_string: The replacement string
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    replace_all: If True, replace all occurrences; if False, replace only the first (default: False)

Returns:
    Success message with number of replacements made

Note:
    Use replace_all=True when the string appears multiple times and you want to replace all.
    Omit replace_all (or set False) for single replacements to avoid unintended changes.
"""
