"""Wiring pins — Section 1 (Checkpoint Cleanup) silent-drop guards.

``phase1-backend.md`` §4.4 (case 55) + the T1.7 MANUAL-ONLY AST pin:

* the SINGLE ``CheckpointCleanupJob(...)`` construction site in
  ``daemon/manager.py`` must carry ``run_lock=`` and ``runs_repo=``
  kwargs (the silent-drop class guard — same rationale as the existing
  ``message_metadata_repo`` pin: a dropped kwarg at the construction
  site silently disables the single-flight gate / audit rows while
  every unit test stays green);
* the AUTO ``execute()`` body must NEVER call ``run_checkpoint_prunes``
  (the manual entry point is MANUAL-ONLY — the auto cycle must never
  route through it; INV-1/INV-9);
* the auto blob-arm call passes NO ``destructive`` kwarg (auto = env
  dual-arm only; INV-1) — the integration suite's case 47 pins the
  behavioral half.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER = REPO_ROOT / "daemon" / "manager.py"
MAINTENANCE = REPO_ROOT / "daemon" / "services" / "maintenance.py"


def _manager_tree() -> ast.Module:
    return ast.parse(MANAGER.read_text(encoding="utf-8"))


def _find_single_construction(tree: ast.Module) -> ast.Call:
    """The single ``CheckpointCleanupJob(...)`` call site."""
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CheckpointCleanupJob"
    ]
    assert len(calls) == 1, (
        f"expected exactly ONE CheckpointCleanupJob construction site in "
        f"manager.py, found {len(calls)} (the wiring pin guards a single "
        "site; multiple sites need multiple pins)"
    )
    return calls[0]


class TestCheckpointCleanupJobWiring:
    def test_single_construction_site_carries_gate_and_repo_kwargs(self):
        """Case 55 — the single construction site passes ``run_lock=`` and
        ``runs_repo=`` (T3 silent-drop guard)."""
        call = _find_single_construction(_manager_tree())
        kwarg_names = {
            kw.arg for kw in call.keywords if kw.arg is not None
        }
        assert "run_lock" in kwarg_names, (
            "CheckpointCleanupJob construction lost the run_lock kwarg — "
            "the auto cycle silently drops out of the single-flight gate "
            "(AM-4)"
        )
        assert "runs_repo" in kwarg_names, (
            "CheckpointCleanupJob construction lost the runs_repo kwarg — "
            "the auto cycle silently stops writing maintenance_runs audit "
            "rows (AM-15)"
        )

    def test_manager_holds_handles_for_service_wiring(self):
        """Companion — the manager stores the shared handles the api.py
        lifespan reads (``_checkpoint_cleanup_job``, ``_maintenance_run_lock``,
        ``_maintenance_runs_repo``) — losing any of them breaks the T5
        wiring with an AttributeError at boot."""
        src = MANAGER.read_text(encoding="utf-8")
        for attr in (
            "self._checkpoint_cleanup_job",
            "self._maintenance_run_lock",
            "self._maintenance_runs_repo",
        ):
            assert attr in src, (
                f"manager.py no longer stores {attr} — the api.py lifespan "
                "MaintenanceApiService wiring depends on it"
            )


class TestManualOnlyEntryPoint:
    def test_auto_execute_never_calls_manual_entry_point(self):
        """T1.7 MANUAL-ONLY pin — the auto ``execute()`` body contains NO
        call to ``run_checkpoint_prunes`` (AST-level, not grep — a comment
        or docstring mention can never satisfy it)."""
        tree = ast.parse(MAINTENANCE.read_text(encoding="utf-8"))
        execute_fn = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "execute"
                and getattr(node, "lineno", 0) > 500  # the JOB's execute, not the service's
            ):
                # Identify by class: find the CheckpointCleanupJob method
                execute_fn = node
        # Robust class-scoped lookup instead of lineno heuristics:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "CheckpointCleanupJob":
                for item in node.body:
                    if isinstance(item, ast.AsyncFunctionDef) and item.name == "execute":
                        execute_fn = item
        assert execute_fn is not None, "CheckpointCleanupJob.execute not found"
        for sub in ast.walk(execute_fn):
            if isinstance(sub, ast.Call):
                target = sub.func
                name = (
                    target.attr if isinstance(target, ast.Attribute) else
                    (target.id if isinstance(target, ast.Name) else None)
                )
                assert name != "run_checkpoint_prunes", (
                    "AUTO execute() must never route through the MANUAL "
                    "entry point run_checkpoint_prunes (INV-1/INV-9 — the "
                    "auto cycle keeps its inline A→E sequence)"
                )

    def test_manual_entry_point_orders_blob_arm_before_row_arm(self):
        """Structural companion to unit case 9a — in
        ``run_checkpoint_prunes`` source order, the ``_prune_unreferenced_blobs``
        call precedes both row-arm calls (AM-2 ordering, textual witness
        for reviewers; the behavioral pin is case 9a)."""
        tree = ast.parse(MAINTENANCE.read_text(encoding="utf-8"))
        fn = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "run_checkpoint_prunes"
            ):
                fn = node
        assert fn is not None, "run_checkpoint_prunes not found"
        blob_calls = [
            sub.lineno
            for sub in ast.walk(fn)
            if isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "_prune_unreferenced_blobs"
        ]
        row_calls = [
            sub.lineno
            for sub in ast.walk(fn)
            if isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in {
                "_prune_per_thread_checkpoints", "_compute_row_prune_dry_run"
            }
        ]
        assert blob_calls and row_calls
        assert min(blob_calls) < min(row_calls), (
            "manual entry point must await the blob arm (Op E) BEFORE the "
            "row arm (Op D) — AM-2 BLOCKING"
        )
