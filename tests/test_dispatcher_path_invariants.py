"""Regression guard: ``enqueue_message_via_jq`` must not be re-introduced.

Phase 6.1 of the cleanup-old-architecture effort consolidated the legacy
``enqueue_message_via_jq`` JobQueue dispatch path into the unified
``enqueue_message`` (with a ``dispatch_path="jobqueue"`` parameter).
This grep-based regression test fails the build if any source or test
file re-introduces the old method name, which would silently bypass
the unified dispatcher.

The legacy name is only allowed to appear inside THIS file (as part of
the assertion message and the grep pattern itself). Any other reference
is a regression.

Run with::

    pytest tests/test_dispatcher_path_invariants.py -v
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


# Where to scan: production code + tests.
SCAN_ROOTS: list[str] = ["daemon", "tests"]

# Paths that legitimately mention the old name as historical reference
# (this file is the regression guard itself — its literal mentions are OK).
SKIP_SUBSTRINGS: tuple[str, ...] = (
    "tests/test_dispatcher_path_invariants.py",
)


def _grep_call_sites() -> list[str]:
    """Run ``grep -rn`` across production code + tests."""
    matches: list[str] = []
    for root in SCAN_ROOTS:
        result = subprocess.run(
            ["grep", "-rn", "enqueue_message_via_jq", root],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 1:  # no matches
            continue
        if result.returncode not in (0, 1):
            raise RuntimeError(
                f"grep failed (exit={result.returncode}): {result.stderr}"
            )
        matches.extend(line for line in result.stdout.splitlines() if line)
    return matches


def _is_comment_only_line(text: str) -> bool:
    """Skip lines that are docstrings or comments (no real call)."""
    stripped = text.lstrip()
    if stripped.startswith("#"):
        return True
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True
    return False


def _extract_real_references(
    raw_lines: list[str], repo_root: Path
) -> list[tuple[str, int, str]]:
    """Filter out comments/docstrings — only return real references."""
    refs: list[tuple[str, int, str]] = []
    for line in raw_lines:
        # Format: ``path:lineno:text``
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        filepath, lineno, text = parts[0], parts[1], parts[2]
        if any(skip in filepath for skip in SKIP_SUBSTRINGS):
            continue
        if _is_comment_only_line(text):
            continue
        # Match actual references (calls, definitions, mock assignments).
        if not re.search(r"(?:^|[^\w])enqueue_message_via_jq", text):
            continue
        # Normalize to repo-relative path for the diagnostic.
        try:
            rel = str(Path(filepath).resolve().relative_to(repo_root))
        except ValueError:
            rel = filepath
        try:
            refs.append((rel, int(lineno), text))
        except ValueError:
            continue
    return refs


def test_enqueue_message_via_jq_not_reintroduced():
    """Phase 6.1: the legacy ``enqueue_message_via_jq`` method must not reappear.

    Scans ``daemon/`` and ``tests/`` for any reference to the legacy
    method (calls, definitions, mock setups). The only allowed match
    is inside this file (the regression guard itself).
    """
    repo_root = Path(__file__).resolve().parent.parent
    raw_lines = _grep_call_sites()
    refs = _extract_real_references(raw_lines, repo_root)

    summary_lines = ["Real references to enqueue_message_via_jq found:"]
    for rel, lineno, text in refs:
        summary_lines.append(f"  {rel}:{lineno}: {text.strip()}")

    assert not refs, (
        "Legacy enqueue_message_via_jq was removed in Phase 6.1. "
        "Use the unified enqueue_message(..., dispatch_path='jobqueue') instead.\n\n"
        + "\n".join(summary_lines)
    )
