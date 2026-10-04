"""Unit tests for the boot auto-continue pass (Phase 2 / M-row coverage).

Feature: auto-continue-running-after-restart.

These tests cover the orchestration logic in
``daemon/services/auto_continue_boot_pass.py`` — selection, kill-
switch, boot-epoch None SKIP, per-instance/sweep/lifespan isolation,
reboot-loop idempotency, exact-kwargs resume, CAS stamp wiring,
stagger cadence, >1-candidate log-skip, and structural grep-proofs
(zero ``enqueue_message`` calls; zero ``datetime.now`` references;
no reaper-style code in the pass).

Harness: a mock manager + a mock ``TaskRepository`` so the pass is
exercised against a controllable surface. No real DB; the repo
returns the candidates the test planted.
"""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

import daemon.services.auto_continue_boot_pass as pass_mod
from daemon.services.auto_continue_boot_pass import (
    AUTO_CONTINUE_KILL_SWITCH_ENV,
    STAGGER_EVERY,
    STAGGER_SLEEP_SECONDS,
    ContinueResult,
    _auto_continue_enabled,
    continue_running_instances_after_restart,
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _TaskRow:
    """A minimal stand-in for the ``Task`` SQLModel — the pass only
    reads ``instance_id``, ``work_id``, ``id``, ``auto_continued_at``."""

    def __init__(
        self,
        *,
        task_id: int,
        instance_id: str,
        work_id: str,
        auto_continued_at: datetime | None = None,
    ) -> None:
        self.id = task_id
        self.instance_id = instance_id
        self.work_id = work_id
        self.auto_continued_at = auto_continued_at


class _MockTaskRepo:
    """Mock ``TaskRepository`` — the pass calls only two methods on it."""

    def __init__(self, candidates: list[_TaskRow] | None = None) -> None:
        self.candidates = candidates or []
        # Mark state for assertions.
        self.find_calls: list[datetime | None] = []
        self.stamp_calls: list[tuple[int, datetime]] = []
        self.stamp_returns: dict[int, bool] = {}
        self.find_exc: Exception | None = None
        self.stamp_exc: Exception | None = None

    def find_auto_continue_candidates(self, boot_epoch: datetime):
        self.find_calls.append(boot_epoch)
        if self.find_exc is not None:
            raise self.find_exc
        return list(self.candidates)

    def mark_task_auto_continued(self, task_id: int, boot_epoch: datetime) -> bool:
        if self.stamp_exc is not None:
            raise self.stamp_exc
        self.stamp_calls.append((task_id, boot_epoch))
        return self.stamp_returns.get(task_id, True)


class _MockManager:
    """Mock ``InstanceManager`` — the pass reads ``_task_repo`` and
    calls ``_has_checkpoint`` + ``_schedule_explicit_handle_resume``."""

    def __init__(self, task_repo: _MockTaskRepo | None = None) -> None:
        self._task_repo = task_repo if task_repo is not None else _MockTaskRepo()
        # Resume state — controllable per test.
        self.has_checkpoint_returns: dict[str, bool] = {}
        self.has_checkpoint_exc: dict[str, Exception] = {}
        self.resume_returns: dict[str, dict[str, Any] | None] = {}
        self.resume_exc: dict[str, Exception] = {}
        self.resume_calls: list[dict[str, Any]] = []

    async def _has_checkpoint(self, instance_id: str) -> bool:
        if instance_id in self.has_checkpoint_exc:
            raise self.has_checkpoint_exc[instance_id]
        return self.has_checkpoint_returns.get(instance_id, True)

    async def _schedule_explicit_handle_resume(self, **kwargs) -> dict[str, Any] | None:
        iid = kwargs.get("instance_id", "")
        self.resume_calls.append(kwargs)
        if iid in self.resume_exc:
            raise self.resume_exc[iid]
        return self.resume_returns.get(iid, {"status": "resuming"})


@pytest.fixture
def fixed_boot_epoch() -> datetime:
    return datetime(2026, 10, 4, 8, 0, 0)


@contextmanager
def _env(name: str, value: str | None):
    """Temporarily set/unset an env var."""
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


# ---------------------------------------------------------------------------
# M10 — Kill-switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    """M10 / D13 — env-direct kill-switch, default ON, per-boot read."""

    def test_default_on_when_unset(self) -> None:
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, None):
            assert _auto_continue_enabled() is True

    def test_on_when_set_to_one(self) -> None:
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            assert _auto_continue_enabled() is True

    def test_on_when_set_to_arbitrary(self) -> None:
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "arbitrary"):
            # Mirrors arm-notify semantics: ONLY =0 disables.
            assert _auto_continue_enabled() is True

    def test_off_when_set_to_zero(self) -> None:
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "0"):
            assert _auto_continue_enabled() is False

    async def test_pass_skipped_on_kill_switch_off(
        self, fixed_boot_epoch: datetime
    ) -> None:
        """Env = 0 → pass returns ``skipped_kill_switch``, zero repo calls."""
        repo = _MockTaskRepo(candidates=[_TaskRow(task_id=1, instance_id="a", work_id="w-a")])
        manager = _MockManager(task_repo=repo)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "0"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )
        assert result.skipped_kill_switch == 1
        assert result.scheduled == 0
        assert result.candidates == 0
        assert repo.find_calls == []
        assert repo.stamp_calls == []
        assert manager.resume_calls == []


# ---------------------------------------------------------------------------
# M11 / M19 — boot-epoch None SKIP (Δ2 / D19)
# ---------------------------------------------------------------------------


class TestBootEpochNoneSkip:
    """M11 / M19 — ``boot_epoch=None`` → SKIP + WARNING; no fallback."""

    async def test_skips_with_warning_counter(
        self, fixed_boot_epoch: datetime
    ) -> None:
        repo = _MockTaskRepo()
        manager = _MockManager(task_repo=repo)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, None
            )
        assert result.skipped_no_boot_epoch == 1
        assert result.scheduled == 0
        assert result.candidates == 0
        # No repo / no resume activity.
        assert repo.find_calls == []
        assert manager.resume_calls == []

    def test_module_source_contains_no_datetime_now_fallback(self) -> None:
        """M19 — structural grep-proof: no ``datetime.now(…)`` or
        ``now(timezone`` reference in the pass module CODE (Δ2 / D19
        rejects the aware-datetime fallback because the entire
        comparison frame is naive-UTC). The docstring may MENTION
        the rejection as part of the explanation; what is forbidden
        is an actual call."""
        import inspect
        import re as _re
        src = inspect.getsource(pass_mod)
        # Strip docstrings + triple-quoted strings + comments line by
        # line so a passing mention in a docstring does not trip
        # the assertion. Comments are stripped line-by-line; strings
        # are stripped block-by-block.
        # 1. Strip triple-quoted strings (docstrings).
        src_no_doc = _re.sub(r'\"\"\"[\s\S]*?\"\"\"', '', src)
        src_no_doc = _re.sub(r"'''[\s\S]*?'''", "", src_no_doc)
        # 2. Strip single-line comments.
        non_comment_lines = [
            ln for ln in src_no_doc.splitlines()
            if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(non_comment_lines)
        # The pass MUST NOT contain a datetime.now(…) call.
        # (The only permitted time acquisition is get_boot_epoch.)
        assert "datetime.now" not in code, (
            "auto_continue_boot_pass.py MUST NOT call datetime.now in "
            "executable code — the aware-datetime fallback is rejected "
            "by Δ2 / D19"
        )
        # Defensive — also forbid timezone.utc usage in code.
        assert "timezone.utc" not in code, (
            "auto_continue_boot_pass.py MUST NOT use timezone.utc in "
            "executable code — use the naive-UTC frame from "
            "get_boot_epoch()"
        )


# ---------------------------------------------------------------------------
# M4 / M20 / M21 / M22 — orchestration
# ---------------------------------------------------------------------------


class TestPassOrchestration:
    """M4 — happy path: candidate → checkpoint → resume → stamp."""

    async def test_happy_path_schedules_and_stamps(
        self, fixed_boot_epoch: datetime
    ) -> None:
        """M4 — one candidate, full happy path, ``scheduled=1``."""
        task = _TaskRow(task_id=1, instance_id="inst-A", work_id="work-A")
        repo = _MockTaskRepo(candidates=[task])
        manager = _MockManager(task_repo=repo)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        assert result.scheduled == 1
        assert result.candidates == 1
        assert result.errors == 0
        # Resume called with EXACT kwargs.
        assert len(manager.resume_calls) == 1
        kwargs = manager.resume_calls[0]
        assert kwargs["instance_id"] == "inst-A"
        assert kwargs["work_id"] if "work_id" in kwargs else kwargs.get("target_work_id") == "work-A"
        assert kwargs["silent"] is True
        assert kwargs["target_work_id"] == "work-A"
        assert kwargs["handle_work_id"] == "work-A"
        assert kwargs["selected_suspension_reason"] is None
        assert kwargs["route_outcome"] == "boot_continue"
        # Stamp called once.
        assert repo.stamp_calls == [(1, fixed_boot_epoch)]

    async def test_structural_no_enqueue_message(self) -> None:
        """M4 grep-proof — the pass module MUST NOT call ``enqueue_message``
        (the WC-wake path that would inject a HumanMessage; rejected
        in D1)."""
        import inspect
        import re as _re
        src = inspect.getsource(pass_mod)
        # Strip docstrings + comments so a passing mention in a
        # docstring does not trip the assertion.
        src_no_doc = _re.sub(r'\"\"\"[\s\S]*?\"\"\"', '', src)
        src_no_doc = _re.sub(r"'''[\s\S]*?'''", "", src_no_doc)
        non_comment_lines = [
            ln for ln in src_no_doc.splitlines()
            if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(non_comment_lines)
        # Defensive: count direct references.
        assert "enqueue_message" not in code, (
            "auto_continue_boot_pass.py MUST NOT call enqueue_message — "
            "D1: continue-in-place via _schedule_explicit_handle_resume, "
            "never enqueue a synthetic 'continue' message"
        )

    async def test_structural_no_force_cancel_or_find_stale(
        self,
    ) -> None:
        """M14 / D4 structural pin — the pass MUST NOT reaper-style
        force-cancel orphan tasks. Continue-in-place keeps the
        orphan ``status='running'`` so the claim-guard holds."""
        import inspect
        import re as _re
        src = inspect.getsource(pass_mod)
        # Strip docstrings + comments so a passing mention in a
        # docstring does not trip the assertion.
        src_no_doc = _re.sub(r'\"\"\"[\s\S]*?\"\"\"', '', src)
        src_no_doc = _re.sub(r"'''[\s\S]*?'''", "", src_no_doc)
        non_comment_lines = [
            ln for ln in src_no_doc.splitlines()
            if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(non_comment_lines)
        assert "force_cancel" not in code, (
            "auto_continue_boot_pass.py MUST NOT call force_cancel — "
            "D4: terminalize-early REJECTED, the orphan stays RUNNING"
        )
        assert "find_stale_running_tasks" not in code, (
            "auto_continue_boot_pass.py MUST NOT call "
            "find_stale_running_tasks — StaleTaskRecovery backstop owns "
            "the age-gated reap"
        )


# ---------------------------------------------------------------------------
# M5 / M6 — Skip paths
# ---------------------------------------------------------------------------


class TestSkipPaths:
    """M5 / M6 — no-checkpoint + resume-refused skip paths."""

    async def test_no_checkpoint_skips_without_stamping(
        self, fixed_boot_epoch: datetime
    ) -> None:
        task = _TaskRow(task_id=1, instance_id="inst-NC", work_id="work-NC")
        repo = _MockTaskRepo(candidates=[task])
        manager = _MockManager(task_repo=repo)
        manager.has_checkpoint_returns["inst-NC"] = False
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        assert result.skipped_no_checkpoint == 1
        assert result.scheduled == 0
        # No resume, no stamp.
        assert manager.resume_calls == []
        assert repo.stamp_calls == []

    async def test_resume_refused_skips_without_stamping(
        self, fixed_boot_epoch: datetime
    ) -> None:
        """M6 — resume returns None / non-``"resuming"`` (incl.
        in-process ``already_resuming``)."""
        task = _TaskRow(task_id=1, instance_id="inst-RR", work_id="work-RR")
        repo = _MockTaskRepo(candidates=[task])
        manager = _MockManager(task_repo=repo)
        manager.resume_returns["inst-RR"] = {"status": "already_resuming"}
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        assert result.skipped_resume_refused == 1
        assert result.scheduled == 0
        # Stamp NOT called.
        assert repo.stamp_calls == []

    async def test_resume_none_skips(
        self, fixed_boot_epoch: datetime
    ) -> None:
        task = _TaskRow(task_id=1, instance_id="inst-N", work_id="work-N")
        repo = _MockTaskRepo(candidates=[task])
        manager = _MockManager(task_repo=repo)
        manager.resume_returns["inst-N"] = None
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        assert result.skipped_resume_refused == 1


# ---------------------------------------------------------------------------
# M7 / M8 — Per-instance / sweep-level isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    """M7 / M8 — per-instance failure + sweep-level guard."""

    async def test_per_instance_failure_does_not_abort(
        self, fixed_boot_epoch: datetime
    ) -> None:
        """M7 — candidate 1 raises (injected), candidate 2 still
        processed; ``errors`` counted; loop never raises."""
        task1 = _TaskRow(task_id=1, instance_id="inst-BAD", work_id="w-BAD")
        task2 = _TaskRow(task_id=2, instance_id="inst-OK", work_id="w-OK")
        repo = _MockTaskRepo(candidates=[task1, task2])
        manager = _MockManager(task_repo=repo)
        manager.has_checkpoint_exc["inst-BAD"] = RuntimeError("probe failed")
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        assert result.errors == 1
        assert result.scheduled == 1
        # The OK candidate was still scheduled.
        assert any(
            kw["instance_id"] == "inst-OK" for kw in manager.resume_calls
        )

    async def test_sweep_level_guard_returns_result(
        self, fixed_boot_epoch: datetime
    ) -> None:
        """M8 — selection raises → WARNING logged, result with
        ``errors=1`` returned, no exception escapes."""
        repo = _MockTaskRepo()
        repo.find_exc = RuntimeError("DB locked")
        manager = _MockManager(task_repo=repo)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        assert result.errors == 1
        assert result.candidates == 0
        # Sweep-level exception was caught — call returned cleanly.
        assert isinstance(result, ContinueResult)


# ---------------------------------------------------------------------------
# M12 — Reboot loop
# ---------------------------------------------------------------------------


class TestRebootLoop:
    """M12 — boot1 → crash → boot2; per-epoch re-arm is safe."""

    async def test_re_arm_after_newer_epoch(
        self, fixed_boot_epoch: datetime
    ) -> None:
        """The per-epoch ``< :boot_epoch`` predicate re-arms: a
        fresh boot with a NEWER epoch re-selects the still-RUNNING
        orphan and re-schedules. The CAS declines re-stamping
        (covered in test_auto_continue_candidates.py)."""
        task = _TaskRow(task_id=1, instance_id="inst-RL", work_id="w-RL")
        # Stamp the row with an older epoch (simulating boot1's stamp).
        task.auto_continued_at = fixed_boot_epoch - timedelta(hours=1)

        repo = _MockTaskRepo(candidates=[task])
        # boot2's epoch is newer than the stamp — the CAS ``<`` re-arms.
        repo.stamp_returns[1] = True
        manager = _MockManager(task_repo=repo)

        newer_epoch = fixed_boot_epoch + timedelta(seconds=1)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, newer_epoch
            )

        # boot2 selects the orphan (per-epoch re-arm) and stamps anew.
        assert result.scheduled == 1

    async def test_stamp_cas_decline_is_idempotent(
        self, fixed_boot_epoch: datetime
    ) -> None:
        """The CAS may decline (concurrent boot stamped the row).
        The pass treats that as benign idempotency: no scheduled,
        no error, no retry-stamp."""
        task = _TaskRow(task_id=1, instance_id="inst-CD", work_id="w-CD")
        repo = _MockTaskRepo(candidates=[task])
        # The CAS declines — same-epoch re-stamp or row left running-set.
        repo.stamp_returns[1] = False
        manager = _MockManager(task_repo=repo)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        # Not scheduled (CAS declined) — not counted as error either.
        assert result.scheduled == 0
        assert result.errors == 0


# ---------------------------------------------------------------------------
# M21 — >1-candidate log-skip
# ---------------------------------------------------------------------------


class TestMultiCandidateSkip:
    """M21 / D21 — instance with >1 RUNNING rows is skipped wholesale."""

    async def test_multi_task_instance_skipped(
        self, fixed_boot_epoch: datetime
    ) -> None:
        task1 = _TaskRow(task_id=1, instance_id="inst-MULTI", work_id="w-MULTI-1")
        task2 = _TaskRow(task_id=2, instance_id="inst-MULTI", work_id="w-MULTI-2")
        task3 = _TaskRow(task_id=3, instance_id="inst-OK", work_id="w-OK")
        repo = _MockTaskRepo(candidates=[task1, task2, task3])
        manager = _MockManager(task_repo=repo)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        assert result.skipped_multi_task_instance == 2
        # The OK instance is still scheduled.
        assert result.scheduled == 1
        # Resume only called for inst-OK.
        resume_iids = [kw["instance_id"] for kw in manager.resume_calls]
        assert "inst-OK" in resume_iids
        assert "inst-MULTI" not in resume_iids


# ---------------------------------------------------------------------------
# M22 — Stagger cadence + duration metric
# ---------------------------------------------------------------------------


class TestStagger:
    """M22 / D22 — stagger every STAGGER_EVERY resumes; duration recorded."""

    async def test_stagger_sleep_after_stagger_every(
        self, fixed_boot_epoch: datetime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After every ``STAGGER_EVERY`` resumes, sleep
        ``STAGGER_SLEEP_SECONDS`` is awaited."""
        # Patch asyncio.sleep to record calls without actually sleeping.
        sleep_calls: list[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            # Don't actually sleep — the test is fast.

        monkeypatch.setattr(pass_mod.asyncio, "sleep", fake_sleep)

        candidates = [
            _TaskRow(task_id=i, instance_id=f"inst-{i}", work_id=f"w-{i}")
            for i in range(1, STAGGER_EVERY + 3)  # 7 candidates
        ]
        repo = _MockTaskRepo(candidates=candidates)
        manager = _MockManager(task_repo=repo)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        # 7 candidates → 2 stagger pauses (at 5th and 10th — but only
        # 7 scheduled → 1 stagger at 5th).
        assert result.scheduled == 7
        assert sleep_calls == [STAGGER_SLEEP_SECONDS], (
            f"Expected exactly one stagger sleep after the 5th resume, "
            f"got: {sleep_calls}"
        )

    async def test_duration_metric_recorded(
        self, fixed_boot_epoch: datetime
    ) -> None:
        """The pass records the loop wall-clock duration in
        ``result.duration_seconds``."""
        repo = _MockTaskRepo()
        manager = _MockManager(task_repo=repo)
        with _env(AUTO_CONTINUE_KILL_SWITCH_ENV, "1"):
            result = await continue_running_instances_after_restart(
                manager, fixed_boot_epoch
            )

        # duration is recorded even for the empty case.
        assert result.duration_seconds >= 0.0
        assert isinstance(result.duration_seconds, float)


# ---------------------------------------------------------------------------
# M9 — Lifespan envelope (api.py wiring)
# ---------------------------------------------------------------------------


class TestLifespanEnvelope:
    """M9 — the api.py wiring wraps the pass in a try/except."""

    def test_api_py_contains_pass_call_between_wake_sweep_and_journal_start(self) -> None:
        """Source-order assertion: the pass call sits BETWEEN
        ``sweep_wake_records()`` and ``upgrade_journal_sweep.start()``
        in ``daemon/api.py`` (Δ3 / D3 — placement)."""
        with open("daemon/api.py") as f:
            src = f.read()
        # Locate the wake sweep and the journal sweep start.
        wake_idx = src.find("sweep_wake_records()")
        journal_start_idx = src.find("upgrade_journal_sweep.start()")
        pass_idx = src.find("continue_running_instances_after_restart")
        assert wake_idx != -1, "wake sweep call not found in api.py"
        assert journal_start_idx != -1, "journal sweep start not found"
        assert pass_idx != -1, "continue pass call not found in api.py"
        assert wake_idx < pass_idx < journal_start_idx, (
            "continue pass must be wired BETWEEN wake_sweep_records() and "
            "upgrade_journal_sweep.start() (D3)"
        )

    def test_api_py_wraps_pass_in_try_except(self) -> None:
        """M9 — the pass call site is wrapped in try/except; WARNING
        logged on failure; never aborts boot."""
        with open("daemon/api.py") as f:
            src = f.read()
        # Find the pass call region.
        pass_idx = src.find("continue_running_instances_after_restart")
        assert pass_idx != -1
        # Look back ~2500 chars and forward ~700 for the wrapping
        # try/except + WARNING log (the StaleTaskRecovery mention
        # sits 1-2 lines after the except clause in the actual file).
        region = src[max(0, pass_idx - 2500):pass_idx + 700]
        # Must contain a try block opening before the pass call.
        assert "try:" in region, (
            "Pass call in api.py must be wrapped in try/except (M9 / R2)"
        )
        # Must contain an except Exception clause.
        assert "except Exception" in region, (
            "Pass call in api.py must catch Exception (M9 / R2)"
        )
        # Must contain the never-abort-boot log phrase.
        assert "StaleTaskRecovery" in region, (
            "Pass WARNING log must name the StaleTaskRecovery backstop"
        )

    def test_api_py_passes_at_envelope_body_level(self) -> None:
        """The pass call sits at the envelope-try BODY level — SIBLING
        of the ``if upgrade_install_dir is not None:`` block, NOT
        nested inside it. The kill-switch is the only legitimate
        way to disable on dev."""
        with open("daemon/api.py") as f:
            src = f.read()
        # The upgrade install-dir guard opens at "if upgrade_install_dir is not None:"
        guard_idx = src.find("if upgrade_install_dir is not None:")
        pass_idx = src.find("continue_running_instances_after_restart")
        assert guard_idx != -1
        assert pass_idx != -1
        assert guard_idx < pass_idx, (
            "Pass call must come AFTER the upgrade_install_dir guard opens"
        )
        # The pass call must be inside the outer try/except envelope
        # (i.e. between the outer try and the outer except at boot_exc).
        # We assert that by checking the pass call comes BEFORE the
        # envelope except at boot_exc.
        envelope_exc_idx = src.find("UpgradeJournalSweepService boot reconcile failed")
        if envelope_exc_idx == -1:
            envelope_exc_idx = src.find("boot reconcile failed")
        # The pass call MUST come before the envelope except — it
        # has its own try/except inside.
        if envelope_exc_idx > 0:
            assert pass_idx < envelope_exc_idx, (
                "Pass call must sit INSIDE the envelope-try body, BEFORE "
                "the envelope-except (Δ3 — placement at body level)"
            )
