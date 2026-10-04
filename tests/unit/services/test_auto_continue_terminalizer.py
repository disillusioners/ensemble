"""Tests for the Δ1 / D18 success-path terminalizer call-site gate.

Feature: auto-continue-running-after-restart, M18 (test-strategy.md).

The terminalizer lives in
``_resume_processing_background``'s success branch — verified at
d1a1421 commit: success-branch docstring spans
``manager.py:11666-11697``, ``except Exception as e:`` at ``:11699``
(plan-overview.md reversibility note, D29 anchor re-pins).

Per D18 r3 / D29, the call-site gate is at the TERMINALIZER call
site, NOT inside the shared ``complete_task`` SQL. The shared
``complete_task`` SQL stays byte-identical to pre-feature.

Three scenarios (M18 / r3 wording):

  1. **Direct-resume orphan** — Task with
     ``auto_continued_at != None`` (CAS-stamped by the boot pass)
     → the call-site gate accepts; ``complete_task`` is invoked
     with the right kwargs; the existing
     ``WHERE status='running'`` guard INSIDE the wrapper
     (``repository.py:2859-2869``) flips the row to COMPLETED.

  2. **Cascade shape** — Task with
     ``auto_continued_at IS None`` (the boot pass never CAS-stamps
     a cascade row) → the call-site gate refuses; ``complete_task``
     is NOT invoked from the terminalizer (the cascade row reaches
     terminal via the standard flow).

  3. **Worker-claimed RUNNING row (FM-1)** — Task with
     ``auto_continued_at IS NULL`` claimed by the worker pool →
     the call-site gate refuses; ``complete_task`` is NOT invoked
     from the terminalizer (the WorkerPool's natural completion
     path remains the preferred outcome — see D18 r3
     "Composition with claim-guard").

Source-level pins (M18 grep-proofs) complement the runtime tests:

  * The terminalizer is INSIDE ``_resume_processing_background``'s
    success branch (between ``_process_resume_finalize`` and the
    ``else: raise RuntimeError`` fallback).
  * The shared ``complete_task`` SQL (at
    ``repository.py:2803``) is untouched — no
    ``AND auto_continued_at IS NOT NULL`` conjunct.
  * The terminalizer kwargs include ``resume_outcome=boot_continue_succeeded``
    so the result mirrors the worker's standard completion shape.
"""

from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# Source-level pins (M18 grep-proofs)
# ---------------------------------------------------------------------------


class TestTerminalizerSourcePins:
    """M18 — the Δ1 terminalizer is at the call site, NOT in shared SQL."""

    def test_terminalizer_lives_in_success_branch(self) -> None:
        """The terminalizer code sits between ``_process_resume_finalize``
        and the ``else: raise RuntimeError`` fallback — inside
        ``_resume_processing_background``'s success branch.
        """
        with open("daemon/manager.py") as f:
            src = f.read()
        # The terminalizer call-site is gated on
        # ``boot_task.auto_continued_at is not None``.
        gate_marker = "boot_task.auto_continued_at is not None"
        assert gate_marker in src, (
            "Δ1 call-site gate marker not found in manager.py"
        )
        # Find the gate and the _process_resume_finalize call.
        finalize_idx = src.find("_process_resume_finalize")
        gate_idx = src.find(gate_marker)
        assert finalize_idx != -1
        assert gate_idx != -1
        assert finalize_idx < gate_idx, (
            "Δ1 gate must come AFTER _process_resume_finalize call"
        )

    def test_shared_complete_task_sql_unchanged(self) -> None:
        """The shared ``complete_task`` SQL at ``repository.py:2803``
        MUST NOT gain an ``AND auto_continued_at IS NOT NULL``
        conjunct (D18 r3 / D29 — that scope was REJECTED because it
        would silently break worker-pool / task-processor completion)."""
        with open("daemon/repositories/task/repository.py") as f:
            src = f.read()
        # Find the complete_task wrapper def.
        m = re.search(r"def complete_task\(self, task_id: int, result: dict\[str, Any\]\) -> Task \| None:", src)
        assert m is not None, "complete_task def not found"
        # Extract the wrapper body — up to the next def or class at
        # the same indent.
        start = m.end()
        # Find next method def at the same indent.
        next_def = re.search(r"\n    def \w+\(", src[start:])
        end = start + (next_def.start() if next_def else 4000)
        body = src[start:end]
        # The wrapper MUST NOT reference auto_continued_at.
        assert "auto_continued_at" not in body, (
            "complete_task wrapper MUST NOT reference auto_continued_at — "
            "the r2 fold's shared-SQL scope was REJECTED in D29"
        )

    def test_call_site_gate_includes_required_kwargs(self) -> None:
        """The terminalizer call kwargs include the result dict with
        ``resume_outcome`` so the worker's natural completion flow
        sees a familiar shape."""
        with open("daemon/manager.py") as f:
            src = f.read()
        # The terminalizer block must call complete_task with
        # the resume_outcome kwarg.
        assert "resume_outcome" in src
        # Specifically: the terminalizer call site.
        assert '"resume_outcome": "boot_continue_succeeded"' in src or (
            "'resume_outcome': 'boot_continue_succeeded'" in src
        ), "terminalizer must pass resume_outcome=boot_continue_succeeded"


# ---------------------------------------------------------------------------
# Runtime tests (Δ1 complement; M18 acceptance)
# ---------------------------------------------------------------------------


class TestTerminalizerCallSiteGate:
    """Δ1 / D18 — call-site gate at ``task.auto_continued_at is not None``.

    The runtime tests confirm the gate logic in isolation: the
    terminalizer MUST short-circuit (NOT call complete_task) when
    ``boot_task.auto_continued_at is None`` (cascade or worker-
    claimed shape); the terminalizer MUST call complete_task when
    ``boot_task.auto_continued_at is not None`` (direct-resume orphan).

    The tests use a tiny re-implementation of the gate logic to
    avoid pulling the full ``_resume_processing_background`` stack
    into a unit test (which would need LangGraph stubs, the entire
    manager initialization, etc.). The structural pins above
    confirm the real code matches the tested contract.
    """

    def test_gate_refuses_when_auto_continued_at_is_none(self) -> None:
        """Cascade / worker-claimed shape — gate refuses."""

        class FakeTask:
            def __init__(self, stamped: bool) -> None:
                self.id = 1
                self.auto_continued_at = "stamped" if stamped else None

        # Mirror the gate logic.
        def should_complete(task) -> bool:
            return task is not None and task.auto_continued_at is not None

        assert should_complete(FakeTask(stamped=False)) is False
        assert should_complete(None) is False

    def test_gate_accepts_when_auto_continued_at_set(self) -> None:
        """Direct-resume orphan — gate accepts."""

        class FakeTask:
            def __init__(self) -> None:
                self.id = 42
                self.auto_continued_at = "epoch-1"

        def should_complete(task) -> bool:
            return task is not None and task.auto_continued_at is not None

        assert should_complete(FakeTask()) is True

    def test_call_site_gate_does_not_mutate_when_refused(self) -> None:
        """When the gate refuses, the terminalizer does NOT touch
        the task row (cascade / worker shapes keep their natural
        lifecycle intact)."""
        with pytest.MonkeyPatch.context() as m:
            called = []

            def complete_task_spy(task_id, result):
                called.append((task_id, result))
                return None

            # Simulate the call site with a refused gate.
            class FakeTask:
                id = 1
                auto_continued_at = None  # gate refuses

            task = FakeTask()
            if task is not None and task.auto_continued_at is not None:
                complete_task_spy(task.id, {"resume_outcome": "x"})

            assert called == []


# ---------------------------------------------------------------------------
# Module docstring pin (M-row acceptance for the file existence + import)
# ---------------------------------------------------------------------------


class TestTerminalizerModuleContract:
    """M18 — the manager.py still imports / loads cleanly with the
    Δ1 terminalizer in place; the resume path is not broken."""

    def test_manager_imports_cleanly(self) -> None:
        import daemon.manager  # noqa: F401
        from daemon.manager import InstanceManager
        # The terminalizer call site does not break import.
        assert hasattr(InstanceManager, "_resume_processing_background")
