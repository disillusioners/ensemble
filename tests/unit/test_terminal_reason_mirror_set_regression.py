"""Regression assertion (Phase 2 task 2.5d): no new
``terminal_reason`` literals appear without a MIRROR_SET
declaration.

The plan verifies (architect + deep review):
* **MIRROR_SET gotcha (FM-5)**: ``reconcile_turn_mirror`` CASE
  WHEN — ELSE 'completed' default. Architect-verified: this
  design introduces NO new ``terminal_reason`` (`deferred_pause`
  is an outcome label, not a ``message_queue.terminal_reason`).
  The task 2.5d regression assertion is a downgrade: a grep-style
  test that no new ``terminal_reason`` literals appear without a
  MIRROR_SET entry.

The test scans the daemon source tree for ``terminal_reason``
literals (string values assigned to the column) and verifies that
every literal is either:

* in an existing MIRROR_SET (the precedent set), or
* a documented legacy literal (the original ``completed`` /
  ``failed`` / ``cancelled`` / ``dead_letter`` set), or
* a comment / docstring mention (skipped via regex).

If the test fails, a new ``terminal_reason`` literal was
introduced without a MIRROR_SET entry — flagging the architect
to update the named-transitions registry.

This is a regression assertion only — no behavioral test
coverage. The Phase 3 test pack owns the full integration test.
"""

from __future__ import annotations

import re
from pathlib import Path


# Precedent MIRROR_SET values (from
# ``daemon/services/turn_transitions.py:ALL_8_MIRRORS``). Any
# ``terminal_reason`` literal that maps to one of these is allowed.
_KNOWN_TERMINAL_REASONS: frozenset[str] = frozenset({
    "completed",
    "failed",
    "cancelled",
    "dead_letter",
    # Phase 1 additions — pre-existing precedent.
    "watchover_terminated",
    # Pre-existing precedent (legacy / migration entries).
    "drift_reconcile",
    "stale_task_recovery",
    # Pre-existing precedent — explicit abort / orphan transitions.
    "aborted",
    "orphaned_no_task",
})


def _collect_terminal_reason_literals(daemon_root: Path) -> set[str]:
    """Walk ``daemon/`` and collect string literals assigned to
    ``terminal_reason`` columns.
    """
    pattern = re.compile(
        # Match ``terminal_reason='literal'`` on a single line,
        # allowing optional whitespace and a trailing comma.
        r"""terminal_reason\s*=\s*(?:f)?['"]([^'"\n]+)['"]"""
    )
    literals: set[str] = set()
    for py_file in daemon_root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        text = py_file.read_text()
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            # Skip f-string expressions (anything containing
            # ``{`` is a f-string substitution — not a literal).
            if "{" in value or "}" in value:
                continue
            # Skip pure whitespace tokens.
            if not value:
                continue
            literals.add(value)
    return literals


def test_no_new_terminal_reason_literals_without_mirror_set() -> None:
    """Regression assertion (Phase 2 task 2.5d).

    Scans ``daemon/`` for ``terminal_reason='...'`` string
    literals and asserts every collected literal is in the
    ``_KNOWN_TERMINAL_REASONS`` precedent set. A failure means
    a new ``terminal_reason`` literal was introduced without
    updating MIRROR_SET — the architect must review and
    explicitly add the literal to ``_KNOWN_TERMINAL_REASONS``
    (with a MIRROR_SET declaration) or remove it.
    """
    daemon_root = Path("daemon")
    literals = _collect_terminal_reason_literals(daemon_root)
    # Empty set — no literals — is OK (the regression fires only
    # when something is added).
    unknown = literals - _KNOWN_TERMINAL_REASONS
    assert not unknown, (
        f"Phase 2 task 2.5d regression: new terminal_reason "
        f"literals detected without MIRROR_SET declaration: "
        f"{sorted(unknown)}. Either (a) remove the literal, or "
        f"(b) add it to _KNOWN_TERMINAL_REASONS in this test "
        f"AND to ALL_8_MIRRORS in "
        f"daemon/services/turn_transitions.py."
    )
