"""Worktree-gate fixture for the hard-reboot-recovery feature.

Feature: hard-reboot recovery. Phase 0 (AC5 / R-07).

This fixture verifies ``import daemon`` resolves INSIDE the dedicated
worktree (``/home/nea/ensemble-src-wt-hard-reboot-recovery``) before
any test runs. It is the implementation-lane invariant that prevents
SILENT tests-on-wrong-tree (R-07): without it, a test run against the
main checkout would import the main tree's ``daemon`` package and
"pass" while proving nothing about this branch.

Shape mirrors the prior-art auto-continue lane
(``git show 00659fba7 -- tests/conftest_worktree.py``): a SILENT no-op
by default, enabled by setting ``ENSEMBLE_HARD_REBOOT_WORKTREE_GATE=1``
— the implementation-lane opt-in. The env gate is what keeps the file
merge-safe: on main / remote VMs that do not have this worktree the
gate never fires, so it cannot falsely fail a checkout.

MERGE-CHECKLIST OBLIGATION (carried from the auto-continue lane,
decisions.md D20 / M23 item 6): this file MUST be REMOVED (or
converted to a permanent no-op) at the merge commit. It is bound to
this worktree's absolute path and must never ship as a live gate to
main / remote VMs. The env gate keeps it inert in the meantime.

Unlike the prior art, this file is WIRED: pytest only auto-loads
plugins named ``conftest.py``, so the prior-art file was dead-as-wired
(it could never refuse anything). It is registered via
``pytest_plugins`` in ``tests/conftest.py``.
"""

from __future__ import annotations

import importlib
import os
import sys


# The worktree path this feature's implementation lane is bound to.
# Cross-referenced with
# ``.agents/shared/planning/hard-reboot-recovery/worktree-claim.txt``.
WORKTREE_PATH = "/home/nea/ensemble-src-wt-hard-reboot-recovery"

#: Operator/implementation-lane opt-in. Absent or != "1" ⇒ silent no-op.
WORKTREE_GATE_ENV = "ENSEMBLE_HARD_REBOOT_WORKTREE_GATE"


def _daemon_resolves_inside_worktree() -> tuple[bool, str]:
    """Check whether ``import daemon`` resolves inside ``WORKTREE_PATH``.

    Returns ``(True, daemon_path)`` when the resolved path is under
    ``WORKTREE_PATH``; ``(False, daemon_path)`` otherwise.

    Force-reimports ``daemon`` so a cached import from a prior test
    (in the same pytest process) does not fool the gate.
    """
    # Drop the cached daemon module + parent packages.
    for mod_name in list(sys.modules):
        if mod_name == "daemon" or mod_name.startswith("daemon."):
            sys.modules.pop(mod_name, None)
    try:
        daemon = importlib.import_module("daemon")
    except Exception as exc:  # pragma: no cover - the import always works
        return (False, f"<import error: {exc!r}>")
    resolved = getattr(daemon, "__file__", None) or "<unknown>"
    return (resolved.startswith(WORKTREE_PATH), resolved)


def pytest_collection_modifyitems(config, items):
    """Verify the worktree-gate before any test runs.

    Runs once at collection time. The gate checks ``import daemon``
    resolves under ``WORKTREE_PATH``; on failure, the entire collection
    FAILS LOUDLY with a clear message that points the operator at the
    planning directory.

    NOTE: this is a SILENT no-op by default. The gate is enabled by
    setting ``ENSEMBLE_HARD_REBOOT_WORKTREE_GATE=1`` — the
    implementation-lane opt-in. Post-merge this file is removed (see
    the merge-checklist obligation in the module docstring), so the
    gate does not falsely fail main / remote VMs.
    """
    if os.environ.get(WORKTREE_GATE_ENV, "0") != "1":
        return

    inside, resolved = _daemon_resolves_inside_worktree()
    if inside:
        return

    msg = (
        f"\n\n=== HARD-REBOOT WORKTREE GATE FAILED ===\n"
        f"import daemon resolves to: {resolved}\n"
        f"expected to be inside:      {WORKTREE_PATH}\n"
        f"\n"
        f"These tests must run against the feature worktree.\n"
        f"Run from: cd {WORKTREE_PATH}\n"
        f"See .agents/shared/planning/hard-reboot-recovery/plan-overview.md\n"
        f"============================================\n"
    )
    # Fail the collection LOUDLY (exit 1) — pytest.exit() with an
    # explicit non-zero returncode is the supported way to abort a
    # session from a collection hook.
    import pytest

    pytest.exit(msg, returncode=1)
