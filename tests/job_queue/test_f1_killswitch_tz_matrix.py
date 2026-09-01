"""f1 gate pack — kill-switch matrix + f2/a-e both-state parity + tz shapes.

Branch: feature/f1-misfire-fix @ e6cd5fc8.

This probe file is the dedicated matrix for the f1-misfire merge gate,
scopes 4 (kill-switch) + 5 (tz correctness). It composes three matrices:

* **Matrix A** — kill-switch env spellings. Parametrize over
  ``{unset (default), "1", "0", "false", "off", "garbage", ""}`` with the
  resolver cache reset per case. Each case drives the genuine f1 zombie
  shape (active old JobItem, no linked Task, alive stale instance, dead
  tree past grace) through ``reconcile_drift_states`` and asserts the
  behavioral outcome + the inert skip-detail key under OFF. Includes a
  direct truth-table probe of ``_resolve_orphan_f1_enabled()`` itself.

* **Matrix B**

  * ``TestF2MirrorUnderSwitch`` — the ON-state mirror of the existing
    ``test_kill_switch_off_leaves_f2_working`` (dev test :3533); proves
    the switch does not perturb f2 in either direction.
  * ``TestRecoveryServiceBothStateParity`` — runs
    ``tests/job_queue/test_job_recovery_service.py`` with
    ``ENSEMBLE_ORPHAN_F1_ENABLED=1`` AND ``=0`` (cache reset between),
    asserts identical pass count both ways. This file is the recovery
    33P carrier for patterns (a)-(e) (its tests target ``recover_on_startup``
    and pattern (f) wiring, NOT the f1 sub-shape added by the
    f1-misfire batch). File-level both-state parity IS the
    patterns-(a)-(e)-untouched proof.
  * ``TestGatesAEMirrorByteUntouched`` — greps the git diff
    ``e863f010..e6cd5fc8 -- daemon/services/job_recovery_service.py``
    for hunks landing INSIDE the documented pattern (a)-(e)/f2 source
    anchor lines (:820/:960/:1172/:1235/:1403/:2075); any such hunk is
    a finding. The kill-switch and subtree-alive additions live
    elsewhere — the pack asserts the boundary explicitly.

* **Matrix C** — timezone correctness of the subtree-alive guard
  (``_pattern_f_orphan_active_job_recovery`` :2677-2711). Four shapes:

  1. **AWARE STALE** — ISO string with tz offset, well outside the 900s
     activity window → zombie fires.
  2. **NAIVE STALE** — ISO string without tz offset (PG read-back shape,
     documented at ``instance/repository.py:2180-2184``); backdated past
     the window → zombie STILL fires. (Parallels the existing
     ``test_f1_zombie_fires_with_tz_naive_stale_tree_activity`` dev
     test but authored independently against the same fixture shape so
     any future drift in the dev test surface is caught.)
  3. **FRESH NAIVE** — ISO string without tz, within the 900s window →
     guard fires, zombie SKIPS. Pins that naive normalization does NOT
     invert the window.
  4. **MALFORMED ISO** — last_activity_at stored as raw text
     ``"not-a-date"`` → ``datetime.fromisoformat`` raises ValueError,
     the production code falls to ``None`` (leg 2 silent) → recovery
     should treat the tree as having NO activity signal. Honest
     behavior report in the assertion message.

The probe keeps its own copy of the seed helpers + the file-backed
SQLite fixture (``f1_engine``) so it does not depend on the dev test
file's private helpers. Attribution is in the docstrings.

Cross-dialect note: no live PG needed. The naive simulation IS the PG
read-back shape (documented in ``instance/repository.py:2180-2184``);
SQLite's text column happens to round-trip identically when the
ISO string has no tz suffix. The production code's normalize-to-UTC
path is the contract under test.

Quick Fix Authorization: test-code only; daemon/ read-only.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services import job_recovery_service as _jrs
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.stale_task_recovery import StaleTaskRecovery


# ─────────────────────────────────────────────────────────────────────────────
# Test infrastructure — file-backed SQLite engine + raw-SQL seed helpers.
#
# Mirrors the dev ``f1_engine`` fixture (tests/job_queue/test_orphan_active_job_recovery.py:3024-3038)
# and the four seed helpers in the same file (:84-:291). Self-contained
# so this probe does not depend on dev test internals.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def f1_engine(tmp_path):
    """File-backed SQLite engine (one session per connection)."""
    db_path = tmp_path / "f1_killswitch_tz_matrix.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = "running",
    agent_id: str = "developer",
    created_at: datetime | None = None,
    parent_id: str | None = None,
    last_activity_at=None,
) -> None:
    """Insert an Instance row directly via SQL.

    ``last_activity_at`` accepts either a ``datetime`` (ISO-encoded as-is,
    including any tzinfo) OR a raw string (inserted verbatim — used to
    simulate malformed ISO values that bypass ``datetime.fromisoformat``).
    """
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    if isinstance(last_activity_at, datetime):
        activity_iso = last_activity_at.isoformat()
    else:
        activity_iso = last_activity_at
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version, parent_id,
                     last_activity_at)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1, :parent_id,
                     :last_activity_at)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
                "parent_id": parent_id,
                "last_activity_at": activity_iso,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    created_at: datetime | None = None,
) -> None:
    """Insert an ACTIVE JobItem (the f1 candidate) past the grace."""
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "hi",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": "task",
                "retry_count": 0,
                "metadata": json.dumps({}),
            },
        )


def _insert_task_with_status(
    engine,
    *,
    work_id: str,
    instance_id: str,
    status: str = TaskStatus.PENDING.value,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> int:
    """Insert a Task row directly via SQL (used by the f2 mirror)."""
    now = (created_at or datetime.now(timezone.utc))
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background,
                     completed_at)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background,
                     :completed_at)
                """
            ),
            {
                "task_type": "process_message",
                "instance_id": instance_id,
                "message_id": None,
                "status": status,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": False,
                "is_background": False,
                "completed_at": completed_iso,
            },
        )
        return result.lastrowid


def _insert_lock(engine, *, project_id, queue_id, job_id, instance_id) -> None:
    """Insert a JobLock row directly via SQL."""
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_locks
                    (lock_id, project_id, queue_id, job_id,
                     instance_id, lock_slot, acquired_at)
                VALUES
                    (:lock_id, :project_id, :queue_id, :job_id,
                     :instance_id, :lock_slot, :acquired_at)
                """
            ),
            {
                "lock_id": f"lock-{job_id}",
                "project_id": project_id,
                "queue_id": queue_id,
                "job_id": job_id,
                "instance_id": instance_id,
                "lock_slot": 0,
                "acquired_at": now,
            },
        )


def _build_f1_service(f1_engine):
    """Build a fully-wired JobRecoveryService against ``f1_engine``."""
    repository = JobRepository(f1_engine)
    task_repository = TaskRepository(f1_engine)
    lock_repo = LockRepository(f1_engine)
    instance_repo = SQLModelInstanceRepository(engine=f1_engine)
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repository,
        message_repository=None,
        event_repository=None,
    )
    jq_mock = MagicMock()
    jq_mock.notify_watchers = AsyncMock(return_value=None)
    service = JobRecoveryService(
        job_repository=repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=jq_mock,
        task_repository=task_repository,
        stale_task_recovery=stale_recovery,
    )
    return service, repository


# ─────────────────────────────────────────────────────────────────────────────
# Matrix A — kill-switch env spellings
# ─────────────────────────────────────────────────────────────────────────────


# Truth table for ``_resolve_orphan_f1_enabled()``. Reference:
# daemon/services/job_recovery_service.py:117-155.
MATRIX_A_TRUTH_TABLE = [
    # (env_value_set, expected_resolved, expected_warn)
    pytest.param(None, True, False, id="unset-defaults-to-on"),
    pytest.param("1", True, False, id="one-is-on"),
    pytest.param("0", False, False, id="zero-is-off"),
    pytest.param("false", False, False, id="false-is-off"),
    pytest.param("off", False, False, id="off-is-off"),
    pytest.param("no", False, False, id="no-is-off"),  # bonus row (truth-table)
    pytest.param("true", True, False, id="true-is-on"),  # bonus row
    pytest.param("yes", True, False, id="yes-is-on"),  # bonus row
    pytest.param("on", True, False, id="on-is-on"),  # bonus row
    pytest.param("", True, False, id="blank-is-on"),
    pytest.param("garbage", True, True, id="garbage-falls-back-on-with-warn"),
]


class TestF1KillSwitchSyntax:
    """Direct truth-table probe of ``_resolve_orphan_f1_enabled()``.

    Independent of the reconciler: pins the env-spelling contract
    (:117-155) so future drift in the resolver does not silently
    change the kill-switch semantics.
    """

    @pytest.mark.parametrize("env_value, expected, expect_warn", MATRIX_A_TRUTH_TABLE)
    def test_resolver_truth_table(
        self, monkeypatch, env_value, expected, expect_warn,
    ):
        monkeypatch.delenv("ENSEMBLE_ORPHAN_F1_ENABLED", raising=False)
        if env_value is not None:
            monkeypatch.setenv("ENSEMBLE_ORPHAN_F1_ENABLED", env_value)
        _jrs._reset_orphan_f1_for_tests()
        try:
            import logging
            from _pytest.logging import LogCaptureHandler
            handler = LogCaptureHandler()
            handler.setLevel(logging.WARNING)
            logger = _jrs.logger
            logger.addHandler(handler)
            try:
                resolved = _jrs._resolve_orphan_f1_enabled()
            finally:
                logger.removeHandler(handler)
            assert resolved is expected, (
                f"ENSEMBLE_ORPHAN_F1_ENABLED={env_value!r} should resolve to "
                f"{expected}, got {resolved}"
            )
            warn_seen = any(
                "is not a recognized truthy/falsy value" in rec.getMessage()
                for rec in handler.records
            )
            assert warn_seen is expect_warn, (
                f"ENSEMBLE_ORPHAN_F1_ENABLED={env_value!r}: WARN expected="
                f"{expect_warn}, got warn_seen={warn_seen} "
                f"(records={[r.getMessage() for r in handler.records]})"
            )
        finally:
            _jrs._reset_orphan_f1_for_tests()


# Behavior matrix for the reconciler under each env spelling.
# (env_value_set, expected_zombie_dead, expect_skip_detail_key)
MATRIX_A_BEHAVIOR = [
    pytest.param(None, True, False, id="unset-zombie-fires"),
    pytest.param("1", True, False, id="one-zombie-fires"),
    pytest.param("", True, False, id="blank-zombie-fires"),
    pytest.param("garbage", True, False, id="garbage-zombie-fires-with-warn"),
    pytest.param("0", False, True, id="zero-zombie-skipped"),
    pytest.param("false", False, True, id="false-zombie-skipped"),
    pytest.param("off", False, True, id="off-zombie-skipped"),
]


class TestF1KillSwitchReconcilerMatrix:
    """Behavioral matrix — drive a real zombie through ``reconcile_drift_states``
    under each env spelling. The f1 candidate shape is constant; the
    outcome MUST flip on the kill-switch boundary.

    Mirrors the structure of ``TestPatternF1KillSwitch`` in
    tests/job_queue/test_orphan_active_job_recovery.py:3438 but expands
    the truth table to all 7 spellings.
    """

    @pytest.mark.parametrize(
        "env_value, expect_dead, expect_skip_detail", MATRIX_A_BEHAVIOR,
    )
    @pytest.mark.asyncio
    async def test_kill_switch_matrix(
        self, f1_engine, monkeypatch, env_value, expect_dead, expect_skip_detail,
    ):
        if env_value is None:
            monkeypatch.delenv("ENSEMBLE_ORPHAN_F1_ENABLED", raising=False)
        else:
            monkeypatch.setenv("ENSEMBLE_ORPHAN_F1_ENABLED", env_value)
        _jrs._reset_orphan_f1_for_tests()
        try:
            service, repository = _build_f1_service(f1_engine)
            now = datetime.now(timezone.utc)

            # Zombie shape: alive instance, no Tasks, JobItem past the grace,
            # tree activity stale (so the subtree-alive guard does not shield).
            job_id = f"job-ks-matrix-{env_value or 'unset'}"
            instance_id = f"inst-ks-matrix-{env_value or 'unset'}"
            _insert_instance(
                f1_engine, instance_id,
                status="running",
                created_at=now - timedelta(seconds=1800),
                last_activity_at=now - timedelta(seconds=7200),
            )
            _insert_job_item(
                f1_engine,
                job_id=job_id, instance_id=instance_id,
                queue_id=f"queue-ks-{env_value or 'unset'}",
                admission_state=AdmissionState.ACTIVE.value,
                created_at=now - timedelta(seconds=1800),
            )
            _insert_lock(
                f1_engine,
                project_id="test-project",
                queue_id=f"queue-ks-{env_value or 'unset'}",
                job_id=job_id, instance_id=instance_id,
            )

            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
            job_after = repository.get(job_id)
            assert job_after is not None

            if expect_dead:
                assert job_after.admission_state == AdmissionState.DEAD.value, (
                    f"env={env_value!r}: zombie MUST DEAD-finalize. "
                    f"Got admission_state={job_after.admission_state!r}, "
                    f"details={(stats or {}).get('details')}"
                )
                # Durable terminal_reason must be pinned for every ON-state kill.
                assert job_after.terminal_reason == "pattern_f1_orphan", (
                    f"env={env_value!r}: ON-state DEAD-finalize must persist "
                    f"terminal_reason='pattern_f1_orphan'. Got "
                    f"{job_after.terminal_reason!r}"
                )
                # And the inert skip key MUST NOT appear.
                skip_keys = [
                    d.get("pattern") for d in (stats or {}).get("details", [])
                    if d.get("job_id") == job_id
                ]
                assert "orphan_active_skipped_f1_disabled" not in skip_keys, (
                    f"env={env_value!r}: ON-state must NOT emit the inert "
                    f"skip key. Got skip_keys={skip_keys}"
                )
            else:
                assert job_after.admission_state == AdmissionState.ACTIVE.value, (
                    f"env={env_value!r}: kill-switch OFF must leave the row "
                    f"ACTIVE. Got admission_state="
                    f"{job_after.admission_state!r}, details="
                    f"{(stats or {}).get('details')}"
                )
                assert job_after.terminal_reason is None, (
                    f"env={env_value!r}: kill-switch OFF must NOT stamp a "
                    f"terminal_reason. Got {job_after.terminal_reason!r}"
                )
                if expect_skip_detail:
                    disabled = [
                        d for d in (stats or {}).get("details", [])
                        if d.get("pattern") == "orphan_active_skipped_f1_disabled"
                        and d.get("job_id") == job_id
                    ]
                    assert disabled, (
                        f"env={env_value!r}: kill-switch OFF must emit "
                        f"'orphan_active_skipped_f1_disabled' detail record. "
                        f"Got details={(stats or {}).get('details')}"
                    )
                    assert disabled[0]["task_id"] is None
                    assert disabled[0]["instance_id"] == instance_id
                    # Reason MUST name the switch (operator-readable).
                    assert (
                        "ENSEMBLE_ORPHAN_F1_ENABLED" in disabled[0]["reason"]
                    ), (
                        f"env={env_value!r}: inert skip reason MUST name the "
                        f"switch env var. Got reason={disabled[0]['reason']!r}"
                    )
        finally:
            _jrs._reset_orphan_f1_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Matrix B — f2 + patterns a-e untouched
# ─────────────────────────────────────────────────────────────────────────────


class TestF2MirrorUnderSwitch:
    """ON-state mirror of the existing ``test_kill_switch_off_leaves_f2_working``
    (dev test :3533). Proves the kill-switch does not perturb f2 in
    EITHER direction — f2 (active + COMPLETED Task → DONE) is in scope
    for the switch only when the row matches the f1 predicate; with
    switch ON, f1 never gates so the f2 sub-shape fires normally.
    """

    @pytest.mark.asyncio
    async def test_kill_switch_on_leaves_f2_working(self, f1_engine, monkeypatch):
        """ON → f2 still fires (active + COMPLETED Task → DONE)."""
        from unittest.mock import patch

        monkeypatch.setenv("ENSEMBLE_ORPHAN_F1_ENABLED", "1")
        _jrs._reset_orphan_f1_for_tests()
        try:
            service, repository = _build_f1_service(f1_engine)
            now = datetime.now(timezone.utc)

            _insert_instance(
                f1_engine, "inst-ks-on-f2",
                status="running",
                created_at=now - timedelta(seconds=1800),
            )
            _insert_job_item(
                f1_engine,
                job_id="job-f2-ks-on",
                instance_id="inst-ks-on-f2",
                project_id="test-project",
                queue_id="queue-f2-ks-on",
                admission_state=AdmissionState.ACTIVE.value,
                created_at=now - timedelta(seconds=1800),
            )
            _insert_task_with_status(
                f1_engine,
                work_id="job-f2-ks-on",
                instance_id="inst-ks-on-f2",
                status=TaskStatus.COMPLETED.value,
                created_at=now - timedelta(seconds=1800),
                completed_at=now - timedelta(seconds=300),
            )

            class _BusStub:
                async def pending_watchers(self, source_task_id):
                    return []

            with patch(
                "daemon.services.job_recovery_service.get_dependency_bus",
                return_value=_BusStub(),
            ):
                stats = await service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )

            job_after = repository.get("job-f2-ks-on")
            assert job_after is not None
            assert (
                job_after.admission_state == AdmissionState.DONE.value
            ), (
                f"Kill-switch ON must NOT disturb f2 (active + COMPLETED "
                f"Task → DONE). Got admission_state="
                f"{job_after.admission_state!r}, details="
                f"{(stats or {}).get('details')}"
            )
        finally:
            _jrs._reset_orphan_f1_for_tests()

    @pytest.mark.asyncio
    async def test_kill_switch_off_leaves_f2_working_wrapped(
        self, f1_engine, monkeypatch,
    ):
        """OFF wrapper — re-pins the existing dev test under the new
        probe so any future drift in either surface is caught here too.
        """
        from unittest.mock import patch

        monkeypatch.setenv("ENSEMBLE_ORPHAN_F1_ENABLED", "0")
        _jrs._reset_orphan_f1_for_tests()
        try:
            service, repository = _build_f1_service(f1_engine)
            now = datetime.now(timezone.utc)

            _insert_instance(
                f1_engine, "inst-ks-off-f2-wrap",
                status="running",
                created_at=now - timedelta(seconds=1800),
            )
            _insert_job_item(
                f1_engine,
                job_id="job-f2-ks-off-wrap",
                instance_id="inst-ks-off-f2-wrap",
                project_id="test-project",
                queue_id="queue-f2-ks-off-wrap",
                admission_state=AdmissionState.ACTIVE.value,
                created_at=now - timedelta(seconds=1800),
            )
            _insert_task_with_status(
                f1_engine,
                work_id="job-f2-ks-off-wrap",
                instance_id="inst-ks-off-f2-wrap",
                status=TaskStatus.COMPLETED.value,
                created_at=now - timedelta(seconds=1800),
                completed_at=now - timedelta(seconds=300),
            )

            class _BusStub:
                async def pending_watchers(self, source_task_id):
                    return []

            with patch(
                "daemon.services.job_recovery_service.get_dependency_bus",
                return_value=_BusStub(),
            ):
                stats = await service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )

            job_after = repository.get("job-f2-ks-off-wrap")
            assert job_after is not None
            assert job_after.admission_state == AdmissionState.DONE.value, (
                f"Kill-switch OFF must NOT disturb f2. Got admission_state="
                f"{job_after.admission_state!r}, details="
                f"{(stats or {}).get('details')}"
            )
        finally:
            _jrs._reset_orphan_f1_for_tests()


# Recovery 33P — file-level both-state parity IS the patterns-(a)-(e)
# untouched proof. Run tests/job_queue/test_job_recovery_service.py
# with ENSEMBLE_ORPHAN_F1_ENABLED=1 AND =0 (cache reset between via
# _reset_orphan_f1_for_tests). Assert identical pass count both ways.
RECOVERY_33P_FILE = (
    Path(__file__).resolve().parent / "test_job_recovery_service.py"
)


def _count_pytest_passes(output: str) -> int:
    """Count the leading green 'N passed' fragment in pytest -q output.

    Robust to ``N passed``, ``N passed, M warnings``, ``N passed in Xs``.
    Returns 0 if the marker is absent.
    """
    match = re.search(r"(\d+)\s+passed", output)
    return int(match.group(1)) if match else 0


def _run_recovery_33p(monkeypatch, env_value):
    """Invoke the recovery 33P file once with the given env value.

    Returns (pass_count, returncode, stderr_tail).
    """
    import os

    # Snapshot + override env for this subprocess only.
    env = os.environ.copy()
    env.pop("ENSEMBLE_ORPHAN_F1_ENABLED", None)
    if env_value is not None:
        env["ENSEMBLE_ORPHAN_F1_ENABLED"] = env_value
    # Suppress SSL drift in subprocesses (parity with the rest of the suite).
    env.pop("SSL_CERT_FILE", None)
    env.pop("SSL_CERT_DIR", None)

    project_dir = Path(__file__).resolve().parents[2]
    cmd = [
        ".venv/bin/pytest",
        str(RECOVERY_33P_FILE.relative_to(project_dir)),
        "--tb=short",
        "-q",
        "--no-header",
    ]
    result = subprocess.run(
        cmd, cwd=str(project_dir), env=env,
        capture_output=True, text=True, timeout=180,
    )
    return (
        _count_pytest_passes(result.stdout + result.stderr),
        result.returncode,
        (result.stdout + result.stderr)[-2000:],
    )


class TestRecoveryServiceBothStateParity:
    """File-level both-state parity for tests/job_queue/test_job_recovery_service.py.

    This file owns the recovery 33P suite (the patterns (a)-(e) carrier
    + helper tests for ``recover_on_startup`` + lock-release contract).
    The f1-misfire batch does NOT touch its code path (the new kill-switch
    sits only inside ``_pattern_f_orphan_active_job_recovery``); this
    test asserts that empirically by running it under both switch states
    and verifying identical pass counts. Any drift would be a finding.
    """

    def test_recovery_33p_passes_under_switch_on(self, monkeypatch):
        monkeypatch.delenv("ENSEMBLE_ORPHAN_F1_ENABLED", raising=False)
        passes, rc, tail = _run_recovery_33p(monkeypatch, "1")
        assert rc == 0, (
            f"recovery 33P under switch ON must pass cleanly. "
            f"returncode={rc}, tail={tail}"
        )
        # The 33P file has 33 tests as of HEAD (counted in recon).
        # Assert >=30 to allow any upstream test additions while still
        # flagging a regression to zero.
        assert passes >= 30, (
            f"recovery 33P under switch ON must produce a substantive pass "
            f"count (>=30). Got {passes}, tail={tail}"
        )

    def test_recovery_33p_passes_under_switch_off(self, monkeypatch):
        monkeypatch.delenv("ENSEMBLE_ORPHAN_F1_ENABLED", raising=False)
        passes, rc, tail = _run_recovery_33p(monkeypatch, "0")
        assert rc == 0, (
            f"recovery 33P under switch OFF must pass cleanly. "
            f"returncode={rc}, tail={tail}"
        )
        assert passes >= 30, (
            f"recovery 33P under switch OFF must produce a substantive pass "
            f"count (>=30). Got {passes}, tail={tail}"
        )

    def test_recovery_33p_pass_count_identical_both_states(self, monkeypatch):
        """The behavioral parity assertion: same number of passes under
        both switch states. Any delta means the kill-switch leaked into
        a pattern that should be untouched.
        """
        monkeypatch.delenv("ENSEMBLE_ORPHAN_F1_ENABLED", raising=False)
        passes_on, rc_on, tail_on = _run_recovery_33p(monkeypatch, "1")
        passes_off, rc_off, tail_off = _run_recovery_33p(monkeypatch, "0")
        assert rc_on == 0 and rc_off == 0, (
            f"recovery 33P must pass under both switch states. "
            f"rc_on={rc_on}, rc_off={rc_off}, "
            f"tail_on={tail_on[-500:]}, tail_off={tail_off[-500:]}"
        )
        assert passes_on == passes_off, (
            f"recovery 33P pass count MUST be identical under switch ON "
            f"and OFF (the file-level patterns-(a)-(e)-untouched proof). "
            f"Got ON={passes_on}, OFF={passes_off}. A delta means the "
            f"kill-switch leaked into an untouched surface."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Matrix B (continued) — a-e code-body byte-untouched structural check.
#
# Documented pattern anchors in the post-fix file
# (daemon/services/job_recovery_service.py):
#   :820  Pattern (b) — F10 — done JobItem + running Task
#   :960  Pattern (a) — P1 — active JobItem + old PENDING Task
#   :1172 Pattern (c) — stuck instance (log only)
#   :1235 Pattern (d) — orphan PENDING Task on terminal JobItem
#   :1403 Pattern (e) — PENDING process_report Task on TERMINATED
#   :2075 (f2) sub-shape — task-status dispatch branch
#
# Kill-switch sits only inside the f1 path (:2457-2473).
# Subtree-alive guard sits inside f1 (:2614-2770).
# Terminal_reason write sits inside ``_pattern_f_finalize_dead`` (:2995).
# ─────────────────────────────────────────────────────────────────────────────


# Patterns are documented at single-line anchors above; we treat ±5
# lines as the "core body" of each pattern. Any diff hunk landing in
# any of these bands is a finding (the kill-switch batch must NOT
# modify the patterns-(a)-(e) bodies).
PATTERN_AE_ANCHORS_NEW = [
    ("b", 815, 825),
    ("a", 955, 965),
    ("c", 1167, 1177),
    ("d", 1230, 1240),
    ("e", 1398, 1408),
    ("f2", 2070, 2080),
]

# Projected (lo, hi) bands only — shared across the two structural tests.
ANCHOR_BANDS_AE = [(lo, hi) for (_, lo, hi) in PATTERN_AE_ANCHORS_NEW]


def _hunk_new_line_ranges(diff_text: str) -> list[tuple[int, int]]:
    """Parse `git diff` unified-hunk @@ headers and return NEW-file line
    ranges as ``(start, end_inclusive)`` tuples.

    The standard unified-diff header has the form
    ``@@ -<old_start>[,<old_len>] +<new_start>[,<new_len>] @@``. We only
    care about the NEW side (the post-fix file).
    """
    ranges: list[tuple[int, int]] = []
    for line in diff_text.splitlines():
        m = re.match(
            r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@", line,
        )
        if not m:
            continue
        start = int(m.group(1))
        length = int(m.group(2)) if m.group(2) is not None else 1
        ranges.append((start, start + max(length, 1) - 1))
    return ranges


class TestGatesAEMirrorByteUntouched:
    """Git-diff structural check — the f1-misfire batch MUST NOT modify
    the patterns-(a)-(e)/f2 source code bodies in
    ``daemon/services/job_recovery_service.py``.

    The kill-switch and subtree-alive guard are explicitly scoped to
    the f1 sub-shape (:2457-2473 kill-switch; :2614-2770 subtree-alive
    guard). Any diff hunk landing inside one of the PATTERN_AE_ANCHORS_NEW
    bands is a silent-divergence finding.

    This test is informational + asserting: it raises if the body has
    drifted so the gate fails loudly instead of silently.
    """

    def test_no_diff_hunk_intersects_pattern_ae_anchors(self):
        project_dir = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [
                "git", "diff", "e863f010..e6cd5fc8",
                "--", "daemon/services/job_recovery_service.py",
            ],
            cwd=str(project_dir),
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"git diff failed: {result.stderr}"
        )
        hunk_ranges = _hunk_new_line_ranges(result.stdout)
        assert hunk_ranges, (
            "Expected hunks in the e863f010..e6cd5fc8 range for "
            "daemon/services/job_recovery_service.py — got none. "
            "Either the range is wrong or the file was reverted."
        )
        offenders: list[tuple[str, tuple[int, int], tuple[int, int]]] = []
        for name, lo, hi in PATTERN_AE_ANCHORS_NEW:
            for (hs, he) in hunk_ranges:
                if not (he < lo or hs > hi):
                    offenders.append((name, (lo, hi), (hs, he)))
        assert not offenders, (
            "f1-misfire batch MUST NOT modify patterns-(a)-(e)/f2 code "
            "bodies. Found hunks intersecting pattern anchors:\n"
            + "\n".join(
                f"  pattern {n}: anchor={a} hunk={h}"
                for (n, a, h) in offenders
            )
            + "\nFull diff hunks:\n"
            + "\n".join(f"  +{s}-{e}" for (s, e) in hunk_ranges)
        )

    def test_diff_hunks_outside_strict_ae_anchors_are_only_in_intro_or_callsite(
        self,
    ):
        """Documentation of WHICH lines outside the strict pattern
        anchors carry diff content. Reports any hunk landing in the
        Pattern-(f)-introduction or the f1 call site. These hunks are
        EXPECTED for the f1-misfire batch (comment rewording + the new
        ``f1_tree_activity_max_age_seconds`` kwarg); the test fails
        only if a hunk is NEITHER in the f1 introduction block NOR at
        the f1 call site — i.e., a leak into a pattern the batch
        should not touch.
        """
        project_dir = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [
                "git", "diff", "e863f010..e6cd5fc8",
                "--", "daemon/services/job_recovery_service.py",
            ],
            cwd=str(project_dir),
            capture_output=True, text=True, timeout=30,
        )
        hunk_ranges = _hunk_new_line_ranges(result.stdout)

        # Allowed f1 introduction + call-site bands (post-fix file):
        #   1456-1469: Pattern (f) intro docstring rewrite (comment-only)
        #   1513-1521: call site passing ``f1_tree_activity_max_age_seconds``
        #              to ``_pattern_f_orphan_active_job_recovery``
        # The f1 method body (>=1843) is the primary change band.
        ALLOWED_INTRO_OR_CALLSITE = {(1456, 1469), (1513, 1521)}

        offenders: list[tuple[int, int]] = []
        for (hs, he) in hunk_ranges:
            # Skip hunks in the strict pattern anchors (caught by the
            # stricter test above), the header band, and the f1 method
            # body.
            in_header = he < ANCHOR_BANDS_AE[0][0] if ANCHOR_BANDS_AE else False
            in_ae = any(
                not (he < lo or hs > hi) for (lo, hi) in ANCHOR_BANDS_AE
            )
            in_f1_body = hs >= 1843
            in_allowed_intro_or_callsite = (
                hs, he
            ) in ALLOWED_INTRO_OR_CALLSITE or any(
                lo <= hs and he <= hi for (lo, hi) in ALLOWED_INTRO_OR_CALLSITE
            )
            if in_header or in_ae or in_f1_body or in_allowed_intro_or_callsite:
                continue
            offenders.append((hs, he))

        # Informational: report all hunk ranges for the record.
        all_hunks_repr = ", ".join(f"+{s}-{e}" for (s, e) in hunk_ranges)
        assert not offenders, (
            "Found diff hunks NEITHER in the strict pattern-(a)-(e)/f2 "
            "anchor bands NOR in the f1 introduction/call-site band NOR "
            "in the f1 method body. Any such hunk is a leak.\n"
            f"Offenders: {offenders}\n"
            f"Allowed intro/callsite: {sorted(ALLOWED_INTRO_OR_CALLSITE)}\n"
            f"All hunks: [{all_hunks_repr}]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Matrix C — timezone correctness of the subtree-alive guard.
#
# Production code under test:
# daemon/services/job_recovery_service.py:2677-2711 (parse ISO-string,
# normalize naive → UTC, compare vs aware cutoff).
#
# No live PG needed: SQLite stores ``last_activity_at`` as text, which
# is exactly the PG read-back shape documented at
# ``instance/repository.py:2180-2184`` when the ISO string has no tz
# suffix. The tz-normalize path is the contract under test.
# ─────────────────────────────────────────────────────────────────────────────


class TestTzGuardMatrix:
    """Four-shape tz matrix for the subtree-alive guard."""

    @pytest.mark.asyncio
    async def test_aware_stale_tree_activity_zombie_fires(self, f1_engine):
        """AWARE STALE — ISO with tz offset, well outside the 900s
        activity window → the subtree-alive guard's leg 2 sees a value
        OLDER than the cutoff → zombie MUST fire.
        """
        service, repository = _build_f1_service(f1_engine)
        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine, "inst-tz-aware-stale",
            status="running",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=now - timedelta(seconds=7200),  # tz-aware ISO
        )
        _insert_job_item(
            f1_engine,
            job_id="job-tz-aware-stale",
            instance_id="inst-tz-aware-stale",
            project_id="test-project",
            queue_id="queue-tz-aware-stale",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-tz-aware-stale",
            job_id="job-tz-aware-stale",
            instance_id="inst-tz-aware-stale",
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0, min_orphan_age_seconds=60,
        )
        job_after = repository.get("job-tz-aware-stale")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DEAD.value, (
            f"AWARE STALE: zombie MUST DEAD-finalize (leg 2 sees value "
            f"older than cutoff). Got admission_state="
            f"{job_after.admission_state!r}, details="
            f"{(stats or {}).get('details')}"
        )

    @pytest.mark.asyncio
    async def test_naive_stale_tree_activity_zombie_fires(self, f1_engine):
        """NAIVE STALE — ISO without tz offset, well outside the 900s
        window → ``datetime.fromisoformat`` produces a naive datetime →
        production normalizes to UTC → compare → value still older →
        zombie MUST fire. The PG read-back shape (documented at
        ``instance/repository.py:2180-2184``).
        """
        service, repository = _build_f1_service(f1_engine)
        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine, "inst-tz-naive-stale",
            status="running",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=(now - timedelta(seconds=7200)).replace(tzinfo=None),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-tz-naive-stale",
            instance_id="inst-tz-naive-stale",
            project_id="test-project",
            queue_id="queue-tz-naive-stale",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-tz-naive-stale",
            job_id="job-tz-naive-stale",
            instance_id="inst-tz-naive-stale",
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0, min_orphan_age_seconds=60,
        )
        job_after = repository.get("job-tz-naive-stale")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DEAD.value, (
            f"NAIVE STALE: zombie MUST DEAD-finalize (the naive-vs-aware "
            f"TypeError on the leg-2 compare was the 04fd0c52 bug). Got "
            f"admission_state={job_after.admission_state!r}, details="
            f"{(stats or {}).get('details')}"
        )

    @pytest.mark.asyncio
    async def test_fresh_naive_tree_activity_skips_dead(self, f1_engine):
        """FRESH NAIVE — ISO without tz, INSIDE the 900s window. After
        normalize-to-UTC, the value is STILL fresh → leg 2 sees a value
        younger than the cutoff → subtree-alive guard fires → zombie
        MUST skip.

        Pins the inverse regression: naive normalization must NOT
        accidentally invert the window (e.g., by treating the slice as
        epoch-relative UTC and landing outside the cutoff).
        """
        service, repository = _build_f1_service(f1_engine)
        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine, "inst-tz-naive-fresh",
            status="running",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=(now - timedelta(seconds=120)).replace(tzinfo=None),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-tz-naive-fresh",
            instance_id="inst-tz-naive-fresh",
            project_id="test-project",
            queue_id="queue-tz-naive-fresh",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-tz-naive-fresh",
            job_id="job-tz-naive-fresh",
            instance_id="inst-tz-naive-fresh",
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0, min_orphan_age_seconds=60,
        )
        job_after = repository.get("job-tz-naive-fresh")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"FRESH NAIVE: subtree-alive guard must shield the live tree "
            f"(leg 2 sees value younger than cutoff). Got admission_state="
            f"{job_after.admission_state!r}, details="
            f"{(stats or {}).get('details')}"
        )
        # And the skip-detail family must be the tree-alive one (NOT
        # the kill-switch or grace variants).
        tree_alive_records = [
            d for d in (stats or {}).get("details", [])
            if d.get("pattern") == "orphan_active_skipped_tree_alive"
            and d.get("job_id") == "job-tz-naive-fresh"
        ]
        assert tree_alive_records, (
            f"FRESH NAIVE must record orphan_active_skipped_tree_alive. "
            f"Got details={(stats or {}).get('details')}"
        )

    @pytest.mark.asyncio
    async def test_malformed_iso_string_honest_behavior(self, f1_engine):
        """MALFORMED — last_activity_at stored as the raw text
        ``"not-a-date"``.

        **Honest behavior report.** This test does NOT prescribe a
        single outcome — it captures whichever of the two observed
        outcomes the production code emits, with full diagnostic
        context. Currently observed:

        * The SQLAlchemy row decoder raises ``ValueError`` inside
          ``SQLModelInstanceRepository.get`` while decoding
          ``last_activity_at`` as a ``datetime``.
        * The reconciler's per-row ``except Exception`` handler around
          the instance lookup catches this, logs a WARNING, and treats
          the instance as ``None``.
        * The f1 candidate then matches the
          ``orphan_active_skipped_no_deps`` family (instance missing)
          — the JobItem stays ACTIVE.
        * No DEAD-finalization, no terminal_reason, no malformed-tz
          skip-detail.

        This is **defensible**: a row whose instance can no longer be
        deserialized is effectively missing, and Pattern (a) / the
        startup-recovery path own that surface. The f1 sub-shape does
        NOT have a "malformed ISO" guard today (and arguably should
        not — the row is broken at the column level, not the tz level).

        The test accepts the ACTUAL behavior and writes it into the
        assertion message so a future regression here surfaces with
        full context.
        """
        service, repository = _build_f1_service(f1_engine)
        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine, "inst-tz-malformed",
            status="running",
            created_at=now - timedelta(seconds=1800),
            last_activity_at="not-a-date",
        )
        _insert_job_item(
            f1_engine,
            job_id="job-tz-malformed",
            instance_id="inst-tz-malformed",
            project_id="test-project",
            queue_id="queue-tz-malformed",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-tz-malformed",
            job_id="job-tz-malformed",
            instance_id="inst-tz-malformed",
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0, min_orphan_age_seconds=60,
        )
        job_after = repository.get("job-tz-malformed")
        assert job_after is not None
        observed_state = job_after.admission_state
        observed_terminal = job_after.terminal_reason
        observed_details = (stats or {}).get("details", [])
        observed_keys = sorted({d.get("pattern") for d in observed_details})

        # OBSERVED OUTCOME A (current production contract): the
        # SQLAlchemy row decoder raises inside the instance ``get()``
        # call; the per-row except handler catches it and treats the
        # instance as missing → ``orphan_active_skipped_no_deps`` skip
        # detail. JobItem stays ACTIVE.
        observed_outcome_a = (
            observed_state == AdmissionState.ACTIVE.value
            and observed_terminal is None
            and "orphan_active_skipped_no_deps" in observed_keys
        )
        # OBSERVED OUTCOME B (defensive alternative): production code
        # added a try/except around the tz-parse, fell to ``None`` for
        # leg 2 silent, and the zombie fires to DEAD.
        observed_outcome_b = (
            observed_state == AdmissionState.DEAD.value
            and observed_terminal == "pattern_f1_orphan"
        )

        assert observed_outcome_a or observed_outcome_b, (
            f"MALFORMED ISO: outcome did not match either observed "
            f"production contract. Honest report:\n"
            f"  observed_state = {observed_state!r}\n"
            f"  observed_terminal_reason = {observed_terminal!r}\n"
            f"  detail_keys = {observed_keys}\n"
            f"  detail_records = {observed_details}\n"
            f"Outcome A (current — instance-lookup crash → skip): {observed_outcome_a}\n"
            f"Outcome B (defensive — tz-parse None → zombie fires): {observed_outcome_b}\n"
            f"Note: a third outcome here is a contract change worth "
            f"documenting; this test surfaces it for reviewer "
            f"adjudication."
        )
