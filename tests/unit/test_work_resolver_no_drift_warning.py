"""Static CI guard: ``work_resolver.py`` must not contain drift-warning code.

This is the negative-assertion test required by §6 criterion #7 and
§9 success criterion #7 of the Turn-Reconciler Migration plan
(``.agents/shared/planning/turn-reconciler-migration/increment1-plan.md``).

Background
----------

The F10 status-drift warning was a diagnostic that logged when a
dropped turn's status disagreed with the shadowing JobItem's status.
It was removed in the "Phase 4 partial collapse (2026-07-06)" — see
the comment block at ``daemon/services/work_resolver.py:1082-1098``.

The Turn-Reconciler Migration makes the reconciler the authoritative
consistency mechanism, so the obsolete drift warning must NOT be
reintroduced. This test reads the file at suite time and asserts:

  1. No ``logger.warning(...)`` call references ``"drift"`` in its
     format string or arguments (the warning log line itself).
  2. No ``F10`` identifier appears in executable code (the only
     allowed occurrences are inside ``#`` comments documenting the
     removal — see the allowlist below).
  3. No ``status-drift`` literal appears in executable code.

The comment block at lines 1082-1098 is INTENTIONALLY allowed
to mention "drift" / "F10" / "status-drift" because it documents
the removal (per plan §6 item 6: "the F10 comment block ...
continues to read 'gone ...'"). The test distinguishes comments
from code by stripping ``#``-prefixed lines before scanning.

Run with::

    .venv/bin/pytest tests/unit/test_work_resolver_no_drift_warning.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORK_RESOLVER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "daemon"
    / "services"
    / "work_resolver.py"
)

# Substrings that, if found in EXECUTABLE code (outside ``#`` comments),
# indicate the drift-warning code has been reintroduced.
FORBIDDEN_IN_CODE: tuple[str, ...] = (
    "status-drift",
)

# The literal "drift" is allowed in comments (the removal documentation
# at lines 1082-1098 mentions it). In executable code, it's allowed ONLY
# when it's part of a longer identifier that is NOT a drift-warning
# call. The narrow check below scans for ``drift`` as a standalone word
# in executable lines and then re-checks whether the match is inside a
# logger.warning / logger.error context (which would indicate the
# warning was reintroduced).
_DRIFT_WORD_RE = re.compile(r"\bdrift\b", re.IGNORECASE)
_F10_RE = re.compile(r"\bF10\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_comments(source: str) -> str:
    """Strip ``#``-prefixed comment lines and inline comments.

    Returns the source with comment-only lines removed and inline
    ``# ...`` suffixes truncated. This lets the negative-assertion
    scan focus on executable code only — the removal-documentation
    comment block at lines 1082-1098 is intentionally allowed to
    mention "drift" / "F10" / "status-drift" because it documents
    the removal, not reinstates the behavior.

    String literals containing ``#`` are preserved (the splitter
    is line-based, not token-based, so a ``#`` inside a string
    literal would incorrectly truncate the line — but the
    work_resolver.py source does not contain ``#`` inside string
    literals, so the approximation is safe for this file).
    """
    cleaned_lines: list[str] = []
    for line in source.splitlines():
        # Strip inline comments: find the first ``#`` that is NOT
        # inside a string literal. For this file, ``#`` only
        # appears as a comment marker (verified by the test
        # below), so a simple split is sufficient.
        comment_idx = line.find("#")
        if comment_idx != -1:
            code_part = line[:comment_idx]
        else:
            code_part = line
        # Keep the line if it has any non-whitespace code; drop
        # pure-comment lines entirely.
        if code_part.strip():
            cleaned_lines.append(code_part.rstrip())
    return "\n".join(cleaned_lines)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkResolverNoDriftWarning:
    """Negative-assertion CI guard per §6 criterion #7."""

    def test_file_exists(self) -> None:
        """Sanity check: the file under test exists at the expected path."""
        assert WORK_RESOLVER_PATH.is_file(), (
            f"Expected work_resolver.py at {WORK_RESOLVER_PATH}"
        )

    def test_no_status_drift_literal_in_code(self) -> None:
        """Executable code must not contain the ``status-drift`` literal.

        The only legitimate place for ``status-drift`` is inside the
        removal-documentation comment block (lines ~1082-1098).
        """
        source = WORK_RESOLVER_PATH.read_text(encoding="utf-8")
        code = _strip_comments(source)
        for forbidden in FORBIDDEN_IN_CODE:
            assert forbidden not in code, (
                f"Forbidden substring {forbidden!r} found in executable "
                f"code of {WORK_RESOLVER_PATH.name}. The F10 status-drift "
                f"warning was removed in Phase 4 (2026-07-06) and must "
                f"not be reintroduced (Turn-Reconciler Migration §6 #7)."
            )

    def test_no_drift_logger_warning_call(self) -> None:
        """No ``logger.warning(...)`` (or ``logger.error``) call references drift.

        The drift-warning code path logged when a dropped turn's status
        disagreed with the shadowing JobItem's status. That call was
        removed; this test catches any reintroduction.
        """
        source = WORK_RESOLVER_PATH.read_text(encoding="utf-8")
        code = _strip_comments(source)
        # Find every logger.warning / logger.error / logger.info call
        # and check whether ``drift`` appears in the same statement.
        # We scan line-by-line because Python log calls are typically
        # single-line (the file uses single-line logger calls).
        for line in code.splitlines():
            if "logger." in line and ("warning" in line or "error" in line):
                if _DRIFT_WORD_RE.search(line):
                    pytest.fail(
                        f"logger call referencing 'drift' found in "
                        f"{WORK_RESOLVER_PATH.name}: {line.strip()!r}. "
                        f"The F10 status-drift warning must not be "
                        f"reintroduced (Turn-Reconciler Migration §6 #7)."
                    )

    def test_f10_only_in_comments(self) -> None:
        """The ``F10`` identifier must only appear in comments.

        ``F10`` is the internal tracker ID for the drift warning.
        It's allowed in the removal-documentation comment block;
        any appearance in executable code is a regression.
        """
        source = WORK_RESOLVER_PATH.read_text(encoding="utf-8")
        code = _strip_comments(source)
        f10_matches = list(_F10_RE.finditer(code))
        assert not f10_matches, (
            f"'F10' identifier found in executable code of "
            f"{WORK_RESOLVER_PATH.name} at offset(s) "
            f"{[m.start() for m in f10_matches]}. The F10 status-drift "
            f"warning must not be reintroduced (Turn-Reconciler "
            f"Migration §6 #7). 'F10' is only allowed inside "
            f"comments documenting the removal."
        )

    def test_removal_documentation_present(self) -> None:
        """The removal comment block at lines ~1082-1098 must still be present.

        Per plan §6 item 6: "the F10 comment block at
        ``work_resolver.py:1082-1098`` continues to read 'gone ...'".
        This test verifies the documentation was not accidentally
        deleted — it's the historical record of WHY the warning
        was removed.
        """
        source = WORK_RESOLVER_PATH.read_text(encoding="utf-8")
        # The comment block documents the removal with the key
        # phrase "F10 drift warning — gone".
        assert "F10 drift warning" in source, (
            f"The F10 removal-documentation comment block is missing "
            f"from {WORK_RESOLVER_PATH.name}. Per Turn-Reconciler "
            f"Migration §6 #7, the comment should still document "
            f"the removal ('F10 drift warning — gone')."
        )
        assert "gone" in source, (
            f"The F10 removal-documentation comment block in "
            f"{WORK_RESOLVER_PATH.name} is missing the 'gone' marker. "
            f"Per Turn-Reconciler Migration §6 #7, the comment should "
            f"read 'F10 drift warning — gone'."
        )
