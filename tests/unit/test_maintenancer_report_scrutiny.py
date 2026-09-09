"""Report-scrutiny marker tests for the maintenancer agent (W2-P4 task 4.7).

Validates that ``agents/maintenancer/workflow.md`` carries the visible
``[REPORT SANITY: …]`` marker pattern (the conditioning language for the
report-scrutiny rule from writing guide §7 + D7). Modeled on
``tests/unit/test_report_integrity_prompts.py`` but scoped to the
single file the spec pins.

Pure file parsing — no daemon/DB startup, no LLM calls.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / "agents" / "maintenancer" / "workflow.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_workflow_md_exists() -> None:
    """workflow.md must exist (per spec task 4.6 — refined dispatcher pattern)."""
    assert WORKFLOW_PATH.exists(), f"workflow.md missing at {WORKFLOW_PATH}"


def test_workflow_has_report_sanity_marker_pattern() -> None:
    """The ``[REPORT SANITY: …]`` marker pattern (with the visible
    bracket + ellipsis) must appear at least once in workflow.md.

    The marker is the conditioning contract for the report-scrutiny
    rule (D7 + writing guide §7) — reports carrying it (or showing
    zero tool-call evidence) are treated as interim, not completion.
    """
    text = _read(WORKFLOW_PATH)
    pattern = re.compile(r"\[REPORT SANITY:.*…\]", re.DOTALL)
    assert pattern.search(text), (
        f"{WORKFLOW_PATH} must contain the '[REPORT SANITY: …]' marker "
        f"pattern (writing guide §7 report-scrutiny contract + D7). "
        f"Found content:\n\n{text[:2000]}"
    )


def test_workflow_has_interim_not_completion_directive() -> None:
    """The directive half of the scrutiny rule — the report is
    ``interim, not completion`` — must appear in workflow.md. The
    marker + directive are the two halves of the contract; the gate
    on the directive enforces both.
    """
    text = _read(WORKFLOW_PATH)
    assert "interim, not completion" in text.lower(), (
        f"{WORKFLOW_PATH} must contain the 'interim, not completion' "
        f"directive (writing guide §7)"
    )


def test_workflow_has_verify_action_naming_send_message() -> None:
    """The verify action named in the scrutiny rule is ``send_message``:
    re-query the worker before acting on its report. Pin the action so
    the report-scrutiny ladder is not silently weakened.
    """
    text = _read(WORKFLOW_PATH)
    assert "send_message" in text, (
        f"{WORKFLOW_PATH} must name 'send_message' as the verify action "
        f"in the report-scrutiny rule (writing guide §7)"
    )


@pytest.mark.parametrize("snippet", [
    "max re-dispatch",
    "1",
])
def test_workflow_states_re_dispatch_cap(snippet: str) -> None:
    """The fan-in escape valve cap (`max re-dispatch = 1`) must be
    stated explicitly in workflow.md (writing guide §7 — "Max re-dispatch
    = 1" is the load-bearing cap that prevents infinite retry loops).
    """
    text = _read(WORKFLOW_PATH)
    assert snippet.lower() in text.lower(), (
        f"{WORKFLOW_PATH} must restate the fan-in escape-valve cap "
        f"({snippet!r} missing)"
    )
