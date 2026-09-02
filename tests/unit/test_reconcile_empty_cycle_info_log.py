"""Empty-cycle reconcile heartbeat observability tests.

Prod incident: ``reconcile_drift_states`` logged its completion at
DEBUG whenever a cycle reconciled nothing — at BOTH completion
sites (the mid-cycle main site after Pattern (d) and the
final-tally site after Pattern (f)). Production runs at INFO, so
healthy cycles were invisible: silence was mistaken for a hung
reconciler.

Fix: promote both completion logs to INFO unconditionally (a
low-noise heartbeat — one line per 300s cycle). These tests pin
the contract:

  * an EMPTY cycle (every repository sweep returns zero rows)
    MUST emit the completion tally line at INFO from BOTH
    completion sites,
  * the line must carry ``reconciled=``/``details=`` so an
    operator grepping INFO can distinguish a healthy empty cycle
    from a hung one.

A/B convention: RED on the pre-fix tree (empty cycle logged at
DEBUG — the ``levelno == INFO`` assertion fails), GREEN on the
post-fix tree.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from daemon.services.job_recovery_service import JobRecoveryService

LOGGER_NAME = "daemon.services.job_recovery_service"
EMPTY_MSG = "reconcile_drift_states: complete — reconciled=0, details=0"


@pytest.fixture
def mock_repositories():
    """Empty-cycle repository wiring: every list-ish sweep the drift
    reconciler runs returns zero rows, so zero patterns reconcile and
    the cycle falls through BOTH completion sites in one call.

    * ``reap_legacy_mirror_zombies`` → [] (Fix B legacy reap — an
      unmocked MagicMock is not iterable and would soft-fail noisily).
    * ``reconcile_terminal_message_mirrors`` → [] (F-1 backstop —
      its result is iterated OUTSIDE the method's try block, so a
      bare MagicMock return would crash the whole cycle).
    * ``list_running_tasks`` / ``list_pending_tasks_older_than`` /
      ``find_processing_jobs`` → [] (Patterns b/a/c/d/f see no rows).
    * ``task_repository.engine = None`` → Pattern (e) hits its
      documented not-wired guard (DEBUG log, return ``None``) instead
      of driving raw SQL against a MagicMock engine.
    """
    job_repo = MagicMock()
    lock_repo = MagicMock()
    instance_repo = MagicMock()
    task_repo = MagicMock()

    job_repo.reap_legacy_mirror_zombies.return_value = []
    job_repo.reconcile_terminal_message_mirrors.return_value = []
    job_repo.find_processing_jobs.return_value = []
    task_repo.list_running_tasks.return_value = []
    task_repo.list_pending_tasks_older_than.return_value = []
    task_repo.engine = None

    return job_repo, lock_repo, instance_repo, task_repo


@pytest.fixture
def service(mock_repositories):
    job_repo, lock_repo, instance_repo, task_repo = mock_repositories
    return JobRecoveryService(
        job_repository=job_repo,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        task_repository=task_repo,
    )


class TestReconcileEmptyCycleHeartbeatInfo:
    """The diagnosis-gap test: a healthy empty reconcile cycle must
    be visible to an operator grepping at INFO."""

    @pytest.mark.asyncio
    async def test_empty_cycle_completes_with_zero_reconciled(
        self, mock_repositories, service
    ):
        """Sanity: the mocked wiring really produces an empty cycle —
        no pattern reconciles anything and no details accumulate."""
        result = await service.reconcile_drift_states()
        assert result == {"reconciled": 0, "details": []}

    @pytest.mark.asyncio
    async def test_empty_cycle_completion_logged_at_info_both_sites(
        self, mock_repositories, service, caplog
    ):
        """Both completion sites emit the tally line at INFO on an
        empty cycle. Pre-fix the empty branch logged at DEBUG, so the
        ``levelno == INFO`` assertion was RED — exactly the gap that
        made the prod incident look like a hang."""
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            result = await service.reconcile_drift_states()

        assert result == {"reconciled": 0, "details": []}

        completion = [
            r
            for r in caplog.records
            if r.name == LOGGER_NAME
            and r.getMessage().startswith(
                "reconcile_drift_states: complete —"
            )
        ]
        assert len(completion) == 2, (
            f"expected exactly 2 completion records (main site + "
            f"final tally), got {len(completion)}: "
            f"{[r.getMessage() for r in completion]}"
        )
        for rec in completion:
            assert rec.levelno == logging.INFO, (
                f"completion logged at "
                f"{logging.getLevelName(rec.levelno)}, expected INFO — "
                f"empty-cycle heartbeat invisible at prod log level"
            )
            assert rec.getMessage() == EMPTY_MSG
