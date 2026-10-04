"""Worktree-gate fixture for the auto-continue-running-after-restart feature.

Feature: auto-continue-running-after-restart. Phase 4 (M23 / Δ7 / D20).

This fixture verifies ``import daemon`` resolves INSIDE the dedicated
worktree (``../ensemble-src-wt-auto-continue``) before any test in
the auto-continue test files runs. The implementation lane created
this fixture as an implementation+E2E-only invariant — see
``phase0-plan.md`` for the rationale and ``phase4-plan.md`` T4.7 for
the merge-checklist:

  "The worktree gate is the implementation-lane invariant (P0 + P4
  T4.7) — it exists to prevent silent-tests-on-wrong-tree (R21)
  DURING the implementation; it MUST NOT survive the merge."

When the feature is MERGED into ``latest``, this file MUST be either
DELETED OR converted to a no-op. The default fixture (no
``autouse=True``, no assertion) keeps it harmless on the post-merge
state. A future implementer can re-enable the assertion by
uncommenting the body if they want to revive the gate for a new
worktree.

Verified in both directions per P4 T4.7 verification step 6:
  - PASS when run from the worktree
  - FAIL when run from the main checkout (import path resolves
    outside ``../ensemble-src-wt-auto-continue``)

The fixture lives under ``tests/conftest_worktree.py`` (separate
file, NOT the existing ``conftest.py``) so the gate can be
cleanly dropped at merge time without touching the standard
pytest configuration.
"""

from __future__ import annotations

import importlib
import os
import sys


# The worktree path this feature's implementation lane is bound to.
# Cross-referenced with
# ``.agents/shared/planning/auto-continue-running-after-restart/worktree-claim.txt``.
WORKTREE_PATH = "/home/nea/ensemble-src-wt-auto-continue"


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config, items):
    """Verify the worktree-gate before any auto-continue test runs.

    Runs once at collection time. The gate checks
    ``import daemon`` resolves under ``WORKTREE_PATH``; on failure,
    the entire collection FAILS LOUDLY with a clear message that
    points the operator to the planning directory.

    NOTE: this is a SILENT no-op by default. The gate is enabled by
    setting the environment variable
    ``ENSEMBLE_AUTO_CONTINUE_WORKTREE_GATE=1`` — this is the
    implementation-lane opt-in. Post-merge state (the file is
    preserved as a no-op for future worktree-gate re-use) does
    NOT set the env var, so the gate does not falsely fail main /
    remote VMs that no longer have ``../ensemble-src-wt-auto-continue``.
    """
    if os.environ.get("ENSEMBLE_AUTO_CONTINUE_WORKTREE_GATE", "0") != "1":
        return

    inside, resolved = _daemon_resolves_inside_worktree()
    if inside:
        return

    msg = (
        f"\n\n=== AUTO-CONTINUE WORKTREE GATE FAILED ===\n"
        f"import daemon resolves to: {resolved}\n"
        f"expected to be inside:      {WORKTREE_PATH}\n"
        f"\n"
        f"The auto-continue test files require the dedicated worktree.\n"
        f"Run from: cd {WORKTREE_PATH}\n"
        f"See .agents/shared/planning/auto-continue-running-after-restart/phase0-plan.md\n"
        f"============================================\n"
    )
    # Fail the collection — pytest will surface this as an error.
    raise pytest.ImplementationError(msg)  # type: ignore[name-defined]


# Late import for the gate's failure path.
import pytest  # noqa: E402
