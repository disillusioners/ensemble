"""Worktree-gate fixture for the hard-reboot-recovery feature.

Feature: hard-reboot recovery. Phase 0 (AC5 / R-07).

This fixture verifies ``import daemon`` resolves INSIDE the dedicated
worktree (``/home/nea/ensemble-src-wt-hard-reboot-recovery``) before
any test runs. It is the implementation-lane invariant that prevents
SILENT tests-on-wrong-tree (R-07): without it, a test run against the
main checkout would import the main tree's ``daemon`` package and
"pass" while proving nothing about this branch.

The resolution is NON-EXECUTING (``importlib.util.find_spec``, no
``sys.modules`` pop, no re-import): SQLAlchemy's declarative registry
is process-global, so re-executing the model modules re-defines the
same tables and raises ``InvalidRequestError`` — which turned the whole
suite red under the armed gate. See :func:`_resolve_daemon_origin`.

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

import importlib.util
import os
import sys


# The worktree path this feature's implementation lane is bound to.
# Cross-referenced with
# ``.agents/shared/planning/hard-reboot-recovery/worktree-claim.txt``.
WORKTREE_PATH = "/home/nea/ensemble-src-wt-hard-reboot-recovery"

#: Operator/implementation-lane opt-in. Absent or != "1" ⇒ silent no-op.
WORKTREE_GATE_ENV = "ENSEMBLE_HARD_REBOOT_WORKTREE_GATE"


def _resolve_daemon_origin() -> str:
    """Resolve where the ``daemon`` package lives — WITHOUT executing it.

    ``importlib.util.find_spec`` LOCATES a top-level package through the
    meta-path finders; it does not import (and therefore does not
    execute) ``daemon/__init__.py``. For an already-imported module it
    returns the cached ``__spec__`` — again without re-executing
    anything.

    Why this matters (the bug this replaced): the earlier version of
    this gate popped every ``daemon*`` entry from ``sys.modules`` and
    re-imported the package, to prove the resolution was not a stale
    cache hit. That RE-EXECUTES the model modules, and SQLAlchemy's
    declarative registry (``SQLModel.metadata``) is PROCESS-GLOBAL — it
    survives the ``sys.modules`` pop. Re-executing the model modules
    therefore re-defines the same tables on the same ``MetaData``
    instance and raises ``InvalidRequestError: Table 'infra_asset_types'
    is already defined for this MetaData instance`` — which turned the
    ENTIRE suite red under the armed gate (16/16 failures). Resolution
    must be non-executing.

    Falls back to ``sys.modules["daemon"].__file__`` when the spec
    carries no origin (namespace packages / stripped specs).
    """
    spec = None
    try:
        spec = importlib.util.find_spec("daemon")
    except (ImportError, ValueError):  # pragma: no cover - defensive
        spec = None
    if spec is not None:
        origin = getattr(spec, "origin", None)
        if origin:
            return str(origin)
        locations = getattr(spec, "submodule_search_locations", None)
        if locations:
            return str(next(iter(locations)))
    module = sys.modules.get("daemon")
    resolved = getattr(module, "__file__", None) if module is not None else None
    return str(resolved) if resolved else "<unknown>"


def _daemon_resolves_inside_worktree() -> tuple[bool, str]:
    """Check whether ``daemon`` resolves inside ``WORKTREE_PATH``.

    Returns ``(True, daemon_path)`` when the resolved path is under
    ``WORKTREE_PATH``; ``(False, daemon_path)`` otherwise.

    The resolution is NON-EXECUTING (see :func:`_resolve_daemon_origin`)
    — a stale ``sys.modules`` cache cannot fool it, and it never
    re-executes the model modules.
    """
    resolved = _resolve_daemon_origin()
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
