"""Path-invariants test: ``enqueue_message_via_jq`` must only be called from documented sites.

This is a single, intentional **grep-based** test. It scans the entire
``daemon/`` tree for any call to ``enqueue_message_via_jq(`` and fails
the build if a new call site appears that is not on the documented
allow-list.

The allow-list exists because ``enqueue_message_via_jq`` is the legacy
JobQueue dispatch path (C-M5 will eventually route this through the
observer; ``daemon/services/job_processor.py`` is referenced in the
spec but does not currently call this method — see allow-list comments).

A direct call from anywhere else means a developer has bypassed the
WorkerPool path / API router / job_continue tool and introduced a new
hidden entry point that won't be tracked by the path-unification work.

Run with::

    pytest tests/test_dispatcher_path_invariants.py -v
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


# Allow-list of files that may contain ``enqueue_message_via_jq(`` call
# sites. Each entry maps the relative path to a brief justification.
#
# Note: ``daemon/services/job_processor.py`` is referenced in the
# C-M5 / decouple-plan documents as the planned orphan-recovery call
# site, but it currently uses ``enqueue_message`` (WorkerPool path)
# instead. It is therefore NOT in the live allow-list. If a future
# change moves orphan recovery onto the JobQueue path, add it here
# AND in the C-M5 plan.
DOCUMENTED_CALL_SITE_FILES: dict[str, str] = {
    "daemon/manager.py": (
        "InstanceManager.enqueue_message_via_jq is the documented "
        "delegation wrapper that forwards to InstanceMessagingService."
    ),
    "daemon/routers/messages.py": (
        "HTTP API route for sending a user message to an instance."
    ),
    "daemon/tools/job_queue.py": (
        "The ``job_continue`` agent tool re-uses the JobQueue entry "
        "point to enqueue a follow-up message on an existing instance."
    ),
}


def _grep_call_sites(grep_root: str) -> list[str]:
    """Run ``grep -rn`` and return raw match lines (``path:lineno:text``)."""
    result = subprocess.run(
        ["grep", "-rn", "enqueue_message_via_jq(", grep_root],
        capture_output=True,
        text=True,
        check=False,
    )
    # grep exit code 1 == no matches (perfectly fine — empty allow-list).
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"grep failed (exit={result.returncode}): {result.stderr}"
        )
    raw = result.stdout.strip()
    if not raw:
        return []
    return [line for line in raw.splitlines() if line]


def _is_definition_line(text: str) -> bool:
    """True for ``def enqueue_message_via_jq`` / ``async def ...`` (skip)."""
    stripped = text.lstrip()
    return stripped.startswith("def enqueue_message_via_jq") or stripped.startswith(
        "async def enqueue_message_via_jq"
    )


def _is_comment_or_docstring_line(text: str) -> bool:
    """Skip lines that are docstrings or comments (no real call)."""
    stripped = text.lstrip()
    if stripped.startswith("#"):
        return True
    # Triple-quoted string fragments / continuations.
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True
    return False


def _extract_call_sites(raw_lines: list[str]) -> list[tuple[str, int, str]]:
    """Filter ``grep`` output to genuine ``enqueue_message_via_jq(`` calls.

    Returns a list of ``(filepath, lineno, text)`` tuples for every
    call site that is NOT a definition, comment, or docstring fragment.
    """
    sites: list[tuple[str, int, str]] = []
    for line in raw_lines:
        # Format: ``path:lineno:text`` (split on first two colons only).
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        filepath, lineno, text = parts[0], parts[1], parts[2]
        if _is_definition_line(text):
            continue
        if _is_comment_or_docstring_line(text):
            continue
        # Must actually be a call (presence of the opening paren after
        # the identifier, possibly with a dot/await preceding it).
        if not re.search(r"(?:^|[^\w])enqueue_message_via_jq\(", text):
            continue
        try:
            sites.append((filepath, int(lineno), text))
        except ValueError:
            continue
    return sites


# ──────────────────────────────────────────────────────────────────────────────
# The test
# ──────────────────────────────────────────────────────────────────────────────


def test_enqueue_message_via_jq_only_documented_call_sites():
    """C3: only documented call sites may invoke ``enqueue_message_via_jq``.

    Scans ``daemon/`` for every call site of ``enqueue_message_via_jq(``,
    strips out the definition itself + comments + docstrings, and
    asserts that **every remaining call** lives in a file on the
    documented allow-list.

    Adding a new caller anywhere else is a regression of the dual-path
    unification invariant and must fail the build.
    """
    # Resolve daemon/ relative to this test file so the test works from
    # any CWD (pytest may chdir before collection).
    repo_root = Path(__file__).resolve().parent.parent
    grep_root = str(repo_root / "daemon")
    assert Path(grep_root).is_dir(), f"daemon/ not found at {grep_root}"

    raw_lines = _grep_call_sites(grep_root)
    call_sites = _extract_call_sites(raw_lines)

    # Each call site must live in a documented file.
    undocumented: list[str] = []
    for filepath, lineno, text in call_sites:
        # Normalize to a path relative to the repo root for matching.
        try:
            rel = str(Path(filepath).resolve().relative_to(repo_root))
        except ValueError:
            rel = filepath
        if rel not in DOCUMENTED_CALL_SITE_FILES:
            undocumented.append(f"{rel}:{lineno}: {text.strip()}")

    # Diagnostic dump: every discovered call site, labeled by whether
    # it is on the allow-list. Helpful when this test fails after a
    # new caller is introduced.
    summary_lines = ["Discovered call sites for enqueue_message_via_jq(:"]
    for filepath, lineno, text in call_sites:
        try:
            rel = str(Path(filepath).resolve().relative_to(repo_root))
        except ValueError:
            rel = filepath
        marker = "OK " if rel in DOCUMENTED_CALL_SITE_FILES else "BAD"
        summary_lines.append(f"  [{marker}] {rel}:{lineno}: {text.strip()}")
    summary_lines.append("")
    summary_lines.append("Documented allow-list:")
    for path, why in DOCUMENTED_CALL_SITE_FILES.items():
        summary_lines.append(f"  - {path}: {why}")

    assert not undocumented, (
        "Undocumented call sites for enqueue_message_via_jq():\n  - "
        + "\n  - ".join(undocumented)
        + "\n\n"
        + "\n".join(summary_lines)
    )
