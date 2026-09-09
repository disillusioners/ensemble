"""Fan-in escape-valve ladder tests for the maintenancer agent (W2-P4 task 4.7).

Validates that ``agents/maintenancer/workflow.md`` carries the full
fan-in escape-valve ladder (writing guide §7 + D7):

  1. Confirm stuck (worker error/crash or staleness signal).
  2. Re-dispatch ONCE (spawn a replacement with the same load_skill).
  3. If still empty/stuck → mark node [incomplete] + deliver ### Gaps.
  4. Max re-dispatch = 1 (two failures = escalate, not retry).

Pure file parsing — no daemon/DB startup, no LLM calls.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path constants
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
# Snippets keyed to the four ladder rows (writing guide §7)
# ---------------------------------------------------------------------------
# Each row of the ladder is a one-line marker. Order is not enforced
# (the test does not require they appear consecutively); the test
# verifies each row is present somewhere in workflow.md.
LADDER_ROWS: tuple[tuple[str, str], ...] = (
    ("confirm stuck", "step 1: confirm the worker is genuinely stuck"),
    ("re-dispatch once", "step 2: re-dispatch ONCE — spawn a replacement"),
    ("[incomplete]", "step 3: mark the node [incomplete]"),
    ("### Gaps", "step 3 continuation: enumerate missing in ### Gaps section"),
    ("max re-dispatch = 1", "step 4: cap on re-dispatches"),
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_workflow_md_exists() -> None:
    """workflow.md must exist (per spec task 4.6)."""
    assert WORKFLOW_PATH.exists(), f"workflow.md missing at {WORKFLOW_PATH}"


@pytest.mark.parametrize(
    "snippet,description",
    LADDER_ROWS,
    ids=[desc for _, desc in LADDER_ROWS],
)
def test_workflow_has_fan_in_ladder_row(snippet: str, description: str) -> None:
    """Each ladder row must appear in workflow.md. Stated as case-
    insensitive substring matches so wording can vary slightly while
    the contract still holds.
    """
    text = _read(WORKFLOW_PATH)
    assert snippet.lower() in text.lower(), (
        f"{WORKFLOW_PATH} missing fan-in escape-valve row: {snippet!r} "
        f"(writing guide §7 + D7). {description}."
    )


def test_fan_in_cap_is_exactly_one() -> None:
    """The cap (``max re-dispatch = 1``) must be present verbatim.

    Pin against softening to "max retries: N" or "1-2 retries" — the
    cap is the load-bearing piece. Allow either ``max re-dispatch =
    1`` or ``max re-dispatch=1`` (whitespace tolerant).
    """
    text = _read(WORKFLOW_PATH)
    pattern = re.compile(r"max\s+re[-\s]?dispatch\s*=\s*1", re.IGNORECASE)
    assert pattern.search(text), (
        f"{WORKFLOW_PATH} must state the fan-in cap verbatim "
        f"('max re-dispatch = 1'); the cap is the load-bearing piece "
        f"that prevents infinite retry loops"
    )
