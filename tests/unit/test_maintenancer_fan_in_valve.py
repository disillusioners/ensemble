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


# ── P5 T5.5 — BR-11: the event-driven lane deadline ─────────────────────────
#
# Home of the BR-11 legs (this file owns the fan-in escape valve; the
# acceptance map CITES these nodes rather than duplicating them).

import ast  # noqa: E402  (kept local to the BR-11 section)

WATCHDOG_PATH = PROJECT_ROOT / "daemon" / "services" / "waiting_children_watchdog.py"
LONG_TOOL_NUDGE_PATH = PROJECT_ROOT / "daemon" / "services" / "long_tool_nudge.py"
RULE_PATH = PROJECT_ROOT / "agents" / "maintenancer" / "rule.md"

#: Executable polling constructs — the orchestrator path must contain NONE.
POLLING_CONSTRUCT_PATTERNS = (
    re.compile(r"\bsleep\s+\d", re.IGNORECASE),
    re.compile(r"\bwhile\s+(?:true|\(true\))", re.IGNORECASE),
    re.compile(r"\buntil\s+\w+\s+(?:reports?|resolves?)", re.IGNORECASE),
    re.compile(r"\bfor\s+_?\s*in\s+range\(", re.IGNORECASE),
)


def _module_str_constant(path: Path, name: str) -> str | None:
    """Return the module-level string constant ``name`` from ``path``.

    AST-only (no import) — the daemon modules are not imported here, so
    this pin never boots anything.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign):
            target = node.targets[0] if node.targets else None
            value = node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and target.id == name
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            return value.value
    return None


def test_workflow_encodes_the_event_driven_deadline() -> None:
    """BR-11 — a lane is ``failed-timeout`` on a NAMED parent wake.

    The two wake sources are the daemon's own watchdog and long-tool
    nudge; their literals must appear in the workflow exactly as the
    daemon defines them.
    """
    text = _read(WORKFLOW_PATH)
    assert "### Lane deadline (BR-11 — event-driven, no polling)" in text, (
        "workflow.md must carry the BR-11 lane-deadline section"
    )
    assert "system:watchdog" in text, (
        "workflow.md must name the watchdog wake source (system:watchdog)"
    )
    assert "system:long-tool-nudge" in text, (
        "workflow.md must name the long-tool-nudge wake source "
        "(system:long-tool-nudge)"
    )
    assert "naming that child" in text, (
        "the BR-11 deadline must be driven by a wake NAMING THAT child"
    )
    assert "failed-timeout" in text, (
        "the BR-11 deadline must name the `failed-timeout` lane status"
    )


def test_the_deadline_covers_the_initial_dispatch_and_its_single_re_dispatch() -> None:
    """BR-11 — a lane wedged on EITHER dispatch is ``failed-timeout``."""
    text = _read(WORKFLOW_PATH)
    assert (
        "The deadline covers the initial dispatch AND its single re-dispatch"
    ) in text, (
        "the BR-11 deadline must cover the INITIAL dispatch AND its single "
        "re-dispatch (a lane wedged on either one is failed-timeout)"
    )


def test_the_valve_is_applied_exactly_once_then_incomplete() -> None:
    """BR-11 — the escape valve is applied ONCE; then ``[incomplete]``."""
    text = _read(WORKFLOW_PATH)
    assert "apply the escape valve once" in text, (
        "the BR-11 deadline must apply the escape valve ONCE"
    )
    assert "mark `[incomplete]` and proceed" in text, (
        "after the single valve application the lane must be marked "
        "`[incomplete]` and the run must proceed"
    )


def test_no_polling_anywhere_in_the_orchestrator_path() -> None:
    """BR-11 — NO polling in the orchestrator path (structure, not prose)."""
    for path in (WORKFLOW_PATH, RULE_PATH):
        text = _read(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern in POLLING_CONSTRUCT_PATTERNS:
                assert not pattern.search(line), (
                    f"{path.name}:{line_no} carries a polling construct "
                    f"({pattern.pattern!r}): {line.strip()!r} — BR-11 forbids "
                    f"polling in the orchestrator path"
                )
    workflow = _read(WORKFLOW_PATH)
    assert "I never poll for child status, never sleep, never wait inside `bash`" in workflow, (
        "workflow.md must state the no-poll rule explicitly (the END TURN "
        "contract forbids polling, sleeping and waiting inside bash)"
    )


def test_the_wake_sources_match_the_daemon_constants() -> None:
    """The prompt literals ARE the daemon's wake-source constants."""
    watchdog = _module_str_constant(WATCHDOG_PATH, "WATCHDOG_SOURCE")
    nudge = _module_str_constant(LONG_TOOL_NUDGE_PATH, "LONG_TOOL_NUDGE_SOURCE")
    assert watchdog == "system:watchdog", (
        f"{WATCHDOG_PATH.name} must define WATCHDOG_SOURCE == 'system:watchdog'; "
        f"got {watchdog!r}"
    )
    assert nudge == "system:long-tool-nudge", (
        f"{LONG_TOOL_NUDGE_PATH.name} must define LONG_TOOL_NUDGE_SOURCE == "
        f"'system:long-tool-nudge'; got {nudge!r}"
    )
    workflow = _read(WORKFLOW_PATH)
    assert watchdog in workflow and nudge in workflow, (
        "workflow.md must use the daemon's EXACT wake-source literals"
    )
