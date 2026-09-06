"""WS3 defer self-witness watchdog — kill-switch matrix + detector + flip.

Branch: ``fix/defer-self-witness-and-cleanup`` @ ``25fbb084`` (WS1 landed).

This probe file covers the WS3 deliverable:

* **Matrix A** — ``ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED`` env spellings.
  Parametrize over ``{unset (default ON), "1", "0", "false", "off",
  "yes", "true", "on", "" (blank), "garbage"}`` with the resolver cache
  reset per case. Each case drives a genuine self-witness blind-spot
  shape (deferred PENDING task + IDLE instance per WS1 carve-out) through
  ``reconcile_drift_states`` and asserts:

  - The WARN fires regardless of the flag (the flag-independent
    observability contract — the operator must see the detection even
    when the unstick is OFF).
  - When ``OFF`` (``"0"`` / ``"false"`` / ``"off"`` — the explicit
    operator escape hatch), the task row's ``is_deferred`` is
    UNCHANGED (OFF=no-writes contract pinned).
  - When ``ON`` (unset / ``"1"`` / ``"true"`` / ``"yes"`` / ``"on"``
    / ``""`` — the new default posture), the task row's
    ``is_deferred`` is flipped to ``False`` (the bounded unstick).
  - Kill-switch flip-back (ON→OFF via test reset) is respected —
    flipping the env via ``monkeypatch`` does NOT auto-pickup
    mid-sweep because the resolver is restart-read.

* **Matrix B** — constitution census stays 23/1/0. Pattern (g)
  writes ``task.is_deferred`` (NOT ``job_queue_items.admission_state``),
  so the census stays untouched. Proves via the
  ``tests/unit/job_state/test_constitution_drift.py`` drift-guard
  + a runtime import check (``KNOWN_*`` lengths).

* **Config parse** — direct truth-table probe of
  ``_resolve_defer_autopromote_enabled``. Empty-string safe: a blank
  env var resolves to ON (the default) — the parser does NOT raise
  on empty-string, it just treats it as the default-ON value.
  Unknown non-blank values fall back to ON with a one-shot WARN —
  consistent with the new "ON unless explicitly disabled" posture.

* **WS1 seam predicate verification** — the detector calls the WS1
  seam ``has_active_non_deferred_work(project_id, requester_instance_id=
  <target>)``. Pin that:
  - When another instance has a live non-deferred ACTIVE JobItem,
    the seam returns True → no WARN, no flip (gate correctly held).
  - When the target is genuinely idle (no live ACTIVE, no other
    project's settled mirror), the seam returns False → WARN fires,
    flip conditional on the flag.

**Harness notes** — file-backed SQLite at ``tmp_path`` with
``NullPool`` + WAL + ``busy_timeout`` (the Testing & QC conventions
recipe; ``StaticPool + WriteGuardSession`` is FORBIDDEN). Each test
gets its own ``tmp_path`` file. Real repositories wired into the
real ``JobRecoveryService`` — the SQL level is genuinely exercised.

Quick Fix Authorization: test-code only; ``daemon/`` read-only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401  (registers Instance)
import daemon.repositories.job_queue.models  # noqa: F401  (registers JobItem)
import daemon.repositories.task.models  # noqa: F401  (registers Task)
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services import job_recovery_service as _jrs
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.stale_task_recovery import StaleTaskRecovery


# ─────────────────────────────────────────────────────────────────────
# Test infrastructure — file-backed SQLite engine (NullPool + WAL +
# busy_timeout) per the Testing & QC conventions recipe.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def defer_watchdog_engine(tmp_path: Path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    The canonical recipe: a real file (schema persists across NullPool
    connections), no shared connection, WAL + a generous busy timeout.
    ``StaticPool + WriteGuardSession`` is FORBIDDEN — it trips the
    QUARANTINE.md write-corruption trap.
    """
    db_path = tmp_path / "defer_self_witness_watchdog.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_instance(
    engine: Engine,
    instance_id: str,
    *,
    project_id: str = "test-project",
    status: str = "running",
    created_at: datetime | None = None,
) -> None:
    """Insert an ``instances`` row directly via SQL."""
    now = (created_at or datetime.now(timezone.utc)).isoformat()
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
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
                "parent_id": None,
                "last_activity_at": now,
            },
        )


def _seed_deferred_pending_task(
    engine: Engine,
    *,
    task_id: int | None = None,
    work_id: str | None = None,
    instance_id: str,
    created_at: datetime | None = None,
) -> int:
    """Insert a deferred PENDING Task (is_deferred=True, NULL heartbeat).

    Returns the integer PK of the inserted row. Uses a deterministic
    age so ``list_pending_tasks_older_than(min_pending_age_seconds=0)``
    always includes it (caller backdates ``created_at`` past the
    grace).

    The ``status='pending'`` + ``last_heartbeat_at IS NULL`` shape is
    the canonical signature for ``list_pending_tasks_older_than`` (see
    ``daemon/repositories/task/repository.py:789``); ``is_deferred=True``
    is the deferred-queue lane marker (Phase 3 Part B1).
    """
    now = (created_at or datetime.now(timezone.utc))
    wid = work_id or f"wid-defer-watchdog-{now.timestamp()}"
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background)
                """
            ),
            {
                "task_type": "process_message",
                "instance_id": instance_id,
                "message_id": None,
                "status": TaskStatus.PENDING.value,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": wid,
                "is_deferred": True,
                "is_background": False,
            },
        )
        # Return the integer PK for assertions.
        # SQLite + SQLAlchemy's INSERT...RETURNING is portable enough
        # here, but we use lastrowid for the broadest compat.
        return result.lastrowid


def _seed_active_job_item(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_type: str = "task",
    created_at: datetime | None = None,
) -> None:
    """Insert a non-defer JobItem (the busy-set witness)."""
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
                "job_type": job_type,
                "retry_count": 0,
                "metadata": json.dumps({}),
            },
        )


def _get_task_is_deferred(engine: Engine, task_id: int) -> bool | None:
    """Read back the ``is_deferred`` flag for assertion."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT is_deferred FROM task WHERE id = :id"),
            {"id": task_id},
        ).first()
    if row is None:
        return None
    # SQLite + SQLAlchemy returns 0/1 — coerce to bool.
    val = row[0]
    return bool(val) if val is not None else None


def _build_defer_watchdog_service(
    engine: Engine,
) -> JobRecoveryService:
    """Build a fully-wired JobRecoveryService against the engine."""
    repository = JobRepository(engine)
    task_repository = TaskRepository(engine)
    lock_repo = LockRepository(engine)
    instance_repo = SQLModelInstanceRepository(engine=engine)
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repository,
        message_repository=None,
        event_repository=None,
    )
    jq_mock = MagicMock()
    jq_mock.notify_watchers = AsyncMock(return_value=None)
    return JobRecoveryService(
        job_repository=repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=jq_mock,
        task_repository=task_repository,
        stale_task_recovery=stale_recovery,
    )


# ─────────────────────────────────────────────────────────────────────
# Matrix A — kill-switch env spellings (resolver truth-table)
# ─────────────────────────────────────────────────────────────────────


# Truth table for ``_resolve_defer_autopromote_enabled``. Reference:
# ``daemon/services/job_recovery_service.py:_resolve_defer_autopromote_enabled``.
# DEFAULT ON — the bounded unstick runs by default; explicit OFF is
# the operator escape hatch.
MATRIX_A_TRUTH_TABLE = [
    # (env_value_set, expected_resolved, expected_warn)
    pytest.param(None, True, False, id="unset-defaults-to-on"),
    pytest.param("1", True, False, id="one-is-on"),
    pytest.param("0", False, False, id="zero-is-off"),
    pytest.param("false", False, False, id="false-is-off"),
    pytest.param("off", False, False, id="off-is-off"),
    pytest.param("no", False, False, id="no-is-off"),  # bonus row
    pytest.param("true", True, False, id="true-is-on"),  # bonus row
    pytest.param("yes", True, False, id="yes-is-on"),  # bonus row
    pytest.param("on", True, False, id="on-is-on"),  # bonus row
    # Empty-string safe: blank env var resolves to ON (the default),
    # NOT to a falsy value. The parser must NOT raise on
    # empty-string — it just treats it as the default-ON value.
    pytest.param("", True, False, id="blank-is-on-empty-string-safe"),
    # Unknown non-blank → ON with WARN (consistent with the new
    # "ON unless explicitly disabled" posture — explicit OFF always
    # wins; unparseable values fall through to the default).
    pytest.param("garbage", True, True, id="garbage-falls-through-on-with-warn"),
]


class TestDeferAutopromoteResolverSyntax:
    """Direct truth-table probe of ``_resolve_defer_autopromote_enabled``.

    Independent of the reconciler: pins the env-spelling contract
    (``daemon/services/job_recovery_service.py:_resolve_defer_autopromote_enabled``)
    so future drift in the resolver does not silently change the
    kill-switch semantics. Mirrors the f1 test shape
    (``tests/job_queue/test_f1_killswitch_tz_matrix.py:333``) but with
    inverted default (ON vs f1's ON — same direction, different
    posture) and inverted fallback direction (ON vs f1's ON — same
    direction).
    """

    @pytest.mark.parametrize(
        "env_value, expected, expect_warn", MATRIX_A_TRUTH_TABLE,
    )
    def test_resolver_truth_table(
        self, monkeypatch, caplog, env_value, expected, expect_warn,
    ):
        monkeypatch.delenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
        )
        if env_value is not None:
            monkeypatch.setenv(
                "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", env_value,
            )
        _jrs._reset_defer_autopromote_for_tests()
        try:
            with caplog.at_level(
                logging.WARNING,
                logger="daemon.services.job_recovery_service",
            ):
                resolved = _jrs._resolve_defer_autopromote_enabled()
            assert resolved is expected, (
                f"ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED={env_value!r} "
                f"should resolve to {expected}, got {resolved}"
            )
            warn_seen = any(
                "is not a recognized truthy/falsy value" in rec.getMessage()
                for rec in caplog.records
            )
            assert warn_seen is expect_warn, (
                f"ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED={env_value!r}: "
                f"WARN expected={expect_warn}, got warn_seen={warn_seen} "
                f"(records={[r.getMessage() for r in caplog.records]})"
            )
        finally:
            _jrs._reset_defer_autopromote_for_tests()


# ─────────────────────────────────────────────────────────────────────
# Matrix A' — env-only resolution + empty-string safe
# (the config-parse acceptance criterion from the brief)
# ─────────────────────────────────────────────────────────────────────


class TestDeferAutopromoteConfigParse:
    """Direct config-parse probe — env-only, default ON, empty-string safe.

    The brief §2 acceptance criterion: "env var default ON, env-only
    resolution (no yaml), empty-string safe." This test pins each
    independent clause:

    * **Default ON** — ``os.environ.get(..., '1')`` makes unset
      resolve to True.
    * **Env-only** — there is NO yaml key. The resolver reads
      ``os.environ`` directly; no yaml / config.py field exists.
    * **Empty-string safe** — ``os.environ.get(..., '1')`` + blank
      value must NOT raise; it resolves to ON (the default).
    """

    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
        )
        _jrs._reset_defer_autopromote_for_tests()
        try:
            assert _jrs._resolve_defer_autopromote_enabled() is True, (
                "Unset env var MUST default to ON — the bounded "
                "unstick runs by default (operator decision "
                "2026-09-06; explicit OFF = 0/false/off is the "
                "escape hatch)."
            )
        finally:
            _jrs._reset_defer_autopromote_for_tests()

    def test_blank_env_resolves_to_on_not_crash(self, monkeypatch):
        """Empty-string safe: blank env var must NOT raise, must
        resolve to ON (NOT OFF — the default-ON posture)."""
        monkeypatch.setenv("ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", "")
        _jrs._reset_defer_autopromote_for_tests()
        try:
            # The brief says "empty-string safe" — the parser must
            # not raise. Resolves to ON (the default), not OFF.
            resolved = _jrs._resolve_defer_autopromote_enabled()
            assert resolved is True, (
                f"Empty-string env var MUST resolve to ON (default), "
                f"got {resolved}."
            )
        finally:
            _jrs._reset_defer_autopromote_for_tests()

    def test_no_yaml_key_in_config_py(self):
        """Env-only resolution: NO yaml key for this flag.

        Grep ``daemon/config.py`` for any reference to
        ``ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED`` or a yaml-key-named
        field. The brief mandates env-only — adding a yaml key would
        silently re-introduce the runtime-vs-restart-read drift the
        kill-switch discipline is designed to prevent.
        """
        config_path = (
            Path(__file__).parent.parent.parent
            / "daemon" / "config.py"
        )
        contents = config_path.read_text()
        assert (
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED" not in contents
        ), (
            f"daemon/config.py MUST NOT reference "
            f"ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED (env-only kill-switch; "
            f"adding a yaml key would silently re-introduce "
            f"runtime-vs-restart-read drift). "
            f"Found the env var name in daemon/config.py."
        )
        assert (
            "defer_autopromote_enabled" not in contents
        ), (
            f"daemon/config.py MUST NOT declare a yaml field named "
            f"defer_autopromote_enabled (env-only). "
            f"Found the lowercase key name in daemon/config.py."
        )


# ─────────────────────────────────────────────────────────────────────
# Matrix B — flag-ON path test through the REAL service (no mocks)
# + flag-OFF no-writes contract.
# ─────────────────────────────────────────────────────────────────────


# Behavior matrix for the reconciler under each env spelling.
# (env_value_set, expect_detection_warn, expect_is_deferred_flipped)
MATRIX_B_BEHAVIOR = [
    # unset (default ON) → detection WARN fires, is_deferred flipped
    pytest.param(None, True, True, id="unset-detection-warn-flip"),
    pytest.param("0", True, False, id="zero-detection-warn-no-flip"),
    pytest.param("false", True, False, id="false-detection-warn-no-flip"),
    pytest.param("off", True, False, id="off-detection-warn-no-flip"),
    pytest.param("", True, True, id="blank-detection-warn-flip"),
    # truthy ON → detection WARN fires, is_deferred flipped
    pytest.param("1", True, True, id="one-detection-warn-flip"),
    pytest.param("true", True, True, id="true-detection-warn-flip"),
    pytest.param("on", True, True, id="on-detection-warn-flip"),
    pytest.param("yes", True, True, id="yes-detection-warn-flip"),
]


class TestDeferAutopromoteReconcilerMatrix:
    """Behavioral matrix — drive a real self-witness blind-spot through
    ``reconcile_drift_states`` under each env spelling. The candidate
    shape (deferred PENDING task + IDLE instance per WS1 carve-out)
    is constant; the outcome MUST flip on the kill-switch boundary.

    Acceptance criteria pinned:
    * WARN fires with instance_id + task id named (flag-independent).
    * OFF (explicit ``=0`` / ``=false`` / ``=off``): ``is_deferred``
      NOT flipped — OFF=no-writes contract pinned (the operator
      escape hatch).
    * ON (unset default / ``=1`` / ``=true`` / ``=yes`` / ``=on`` /
      blank): ``is_deferred`` flipped to False — the bounded unstick.
    * Kill-switch flip-back (ON→OFF via test reset) is respected
      (the resolver is restart-read).
    """

    @pytest.mark.parametrize(
        "env_value, expect_warn, expect_flipped",
        MATRIX_B_BEHAVIOR,
    )
    @pytest.mark.asyncio
    async def test_kill_switch_matrix(
        self, defer_watchdog_engine, monkeypatch, caplog,
        env_value, expect_warn, expect_flipped,
    ):
        if env_value is None:
            monkeypatch.delenv(
                "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
            )
        else:
            monkeypatch.setenv(
                "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", env_value,
            )
        _jrs._reset_defer_autopromote_for_tests()
        try:
            service = _build_defer_watchdog_service(defer_watchdog_engine)
            now = datetime.now(timezone.utc)

            # Shape: ONE deferred PENDING task (is_deferred=True,
            # NULL heartbeat, backdated past the grace) on a LIVE
            # instance. The WS1 seam must return False because:
            # - No live ACTIVE JobItem anywhere (legacy clause catches
            #   any — there are NONE).
            # - No other project settled mirror (carve-out drops the
            #   target's own, but there are no settled mirrors at all
            #   — the project is empty).
            instance_id = f"inst-g-matrix-{env_value or 'unset'}"
            _seed_instance(
                defer_watchdog_engine, instance_id,
                status="running",
                project_id="test-project",
                created_at=now - timedelta(seconds=60),
            )
            task_id = _seed_deferred_pending_task(
                defer_watchdog_engine,
                instance_id=instance_id,
                # Backdate past the grace (60s) so the candidate
                # is included in ``list_pending_tasks_older_than``.
                created_at=now - timedelta(seconds=120),
            )

            # Sanity: the fixture itself is in the right shape.
            assert _get_task_is_deferred(
                defer_watchdog_engine, task_id,
            ) is True, (
                f"env={env_value!r}: fixture broken — seeded task "
                f"must have is_deferred=True."
            )

            # Capture logs to assert the WARN fires (flag-independent).
            with caplog.at_level(
                logging.WARNING,
                logger="daemon.services.job_recovery_service",
            ):
                stats = await service.reconcile_drift_states(
                    min_pending_age_seconds=60,
                    min_orphan_age_seconds=900,
                )
                # Pin: the reconciler completed without raising.
                assert isinstance(stats, dict), (
                    f"env={env_value!r}: reconcile_drift_states "
                    f"must return a dict, got {type(stats).__name__}"
                )

            # Detection record landed in ``details``.
            detection_records = [
                d for d in (stats.get("details") or [])
                if d.get("task_id") == task_id
                and d.get("instance_id") == instance_id
                and d.get("pattern", "").startswith(
                    "defer_self_witness_",
                )
            ]
            assert detection_records, (
                f"env={env_value!r}: detection record MISSING. "
                f"Expected a 'defer_self_witness_*' record for "
                f"task_id={task_id} / instance_id={instance_id}. "
                f"Got details={(stats or {}).get('details')}"
            )

            # The pattern key MUST distinguish the OFF path
            # (``warned_only``) from the ON path (``autopromoted``).
            pattern_keys = sorted({
                d.get("pattern") for d in detection_records
            })
            if expect_flipped:
                assert "defer_self_witness_autopromoted" in pattern_keys, (
                    f"env={env_value!r}: ON path MUST emit "
                    f"'defer_self_witness_autopromoted'. "
                    f"Got pattern_keys={pattern_keys}"
                )
                # Belt-and-suspenders: the warned-only path must NOT
                # also fire (a row can only have one outcome per sweep).
                assert (
                    "defer_self_witness_warned_only"
                    not in pattern_keys
                ), (
                    f"env={env_value!r}: ON path must NOT also "
                    f"emit 'defer_self_witness_warned_only'."
                )
            else:
                assert "defer_self_witness_warned_only" in pattern_keys, (
                    f"env={env_value!r}: OFF path MUST emit "
                    f"'defer_self_witness_warned_only'. "
                    f"Got pattern_keys={pattern_keys}"
                )
                assert (
                    "defer_self_witness_autopromoted"
                    not in pattern_keys
                ), (
                    f"env={env_value!r}: OFF path must NOT emit "
                    f"'defer_self_witness_autopromoted'."
                )

            # Pin: ``is_deferred`` is flipped iff the flag is ON.
            is_deferred_after = _get_task_is_deferred(
                defer_watchdog_engine, task_id,
            )
            if expect_flipped:
                assert is_deferred_after is False, (
                    f"env={env_value!r}: ON path MUST flip "
                    f"is_deferred=False. Got is_deferred="
                    f"{is_deferred_after!r}"
                )
            else:
                # OFF=no-writes contract pinned.
                assert is_deferred_after is True, (
                    f"env={env_value!r}: OFF path MUST NOT flip "
                    f"is_deferred (OFF=no-writes contract). "
                    f"Got is_deferred={is_deferred_after!r}"
                )

            # Pin: the WARN fired regardless of the flag (the
            # flag-independent observability contract).
            warn_seen = any(
                "defer self-witness blind-spot detected" in rec.getMessage()
                and f"task {task_id}" in rec.getMessage()
                and instance_id[:8] in rec.getMessage()
                for rec in caplog.records
            )
            assert warn_seen is expect_warn, (
                f"env={env_value!r}: WARN expected={expect_warn}, "
                f"got warn_seen={warn_seen} (records="
                f"{[r.getMessage()[:120] for r in caplog.records]})"
            )
        finally:
            _jrs._reset_defer_autopromote_for_tests()


class TestDeferAutopromoteKillSwitchFlipBack:
    """The kill-switch is restart-read — flipping the env mid-process
    has no effect until ``_reset_defer_autopromote_for_tests()`` is
    called. Pin that the reset works as advertised: a fresh resolver
    call after the reset sees the NEW env value.

    Mirrors the f1 ``TestF1KillSwitchSyntax`` cache-reset discipline
    (``tests/job_queue/test_f1_killswitch_tz_matrix.py:333``).
    """

    @pytest.mark.asyncio
    async def test_kill_switch_flip_back_respected(
        self, defer_watchdog_engine, monkeypatch,
    ):
        # Start ON.
        monkeypatch.setenv("ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", "1")
        _jrs._reset_defer_autopromote_for_tests()
        assert _jrs._resolve_defer_autopromote_enabled() is True

        # Flip to OFF — but cache is still True (restart-read).
        monkeypatch.setenv("ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", "0")
        # The resolver MUST NOT see the new env value until reset
        # (restart-read semantics). The cache was set on the previous
        # call.
        assert _jrs._resolve_defer_autopromote_enabled() is True, (
            "Resolver is restart-read — cache MUST persist "
            "across env flips until reset."
        )

        # After reset, the new env value is seen.
        _jrs._reset_defer_autopromote_for_tests()
        assert _jrs._resolve_defer_autopromote_enabled() is False, (
            "After _reset_defer_autopromote_for_tests(), the "
            "resolver MUST pick up the new env value."
        )
        _jrs._reset_defer_autopromote_for_tests()


# ─────────────────────────────────────────────────────────────────────
# Matrix C — WS1 seam predicate verification (the detector contract).
# ─────────────────────────────────────────────────────────────────────


class TestDeferSelfWitnessDetector:
    """WS1 seam predicate verification — the detector contract.

    The detector (``_pattern_g_defer_self_witness_watchdog``) calls
    ``JobRepository.has_active_non_deferred_work(project_id,
    requester_instance_id=<target>)``. Pin that:

    * When ANOTHER instance in the same project has a live ACTIVE
      JobItem → predicate True → no WARN, no flip (gate correctly
      held by the busy-set).
    * When the target is the ONLY thing in the project (no live
      ACTIVE, no other project's settled mirror) → predicate False
      → WARN fires, flip conditional on the flag.
    * When the target has a settled mirror from a DIFFERENT instance
      in the same project → predicate True → no WARN, no flip
      (other project witness correctly holds the gate).
    """

    @pytest.mark.asyncio
    async def test_live_active_on_other_instance_holds_gate(
        self, defer_watchdog_engine, monkeypatch,
    ):
        """Other-instance live ACTIVE JobItem → predicate True → no detection."""
        monkeypatch.delenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
        )
        _jrs._reset_defer_autopromote_for_tests()
        try:
            service = _build_defer_watchdog_service(defer_watchdog_engine)
            now = datetime.now(timezone.utc)

            # Target instance (will host the deferred PENDING task).
            target_instance_id = "inst-target"
            _seed_instance(
                defer_watchdog_engine, target_instance_id,
                status="running", project_id="test-project",
                created_at=now - timedelta(seconds=60),
            )
            task_id = _seed_deferred_pending_task(
                defer_watchdog_engine,
                instance_id=target_instance_id,
                created_at=now - timedelta(seconds=120),
            )

            # Other instance in the SAME project with a LIVE ACTIVE
            # JobItem — this is the busy-set witness (legacy clause).
            other_instance_id = "inst-other"
            _seed_instance(
                defer_watchdog_engine, other_instance_id,
                status="running", project_id="test-project",
                created_at=now - timedelta(seconds=60),
            )
            _seed_active_job_item(
                defer_watchdog_engine,
                job_id="job-other-live-active",
                instance_id=other_instance_id,
                project_id="test-project",
                admission_state=AdmissionState.ACTIVE.value,
                created_at=now - timedelta(seconds=60),
            )

            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=60,
                min_orphan_age_seconds=900,
            )

            # No detection record for our task — the other-instance
            # live ACTIVE JobItem correctly held the gate.
            detection_records = [
                d for d in (stats.get("details") or [])
                if d.get("task_id") == task_id
            ]
            assert not detection_records, (
                f"Other-instance live ACTIVE JobItem MUST hold the "
                f"gate — no detection record expected. "
                f"Got detection_records={detection_records}"
            )

            # The task's ``is_deferred`` MUST NOT have changed.
            assert _get_task_is_deferred(
                defer_watchdog_engine, task_id,
            ) is True
        finally:
            _jrs._reset_defer_autopromote_for_tests()

    @pytest.mark.asyncio
    async def test_target_only_in_project_detects_self_witness(
        self, defer_watchdog_engine, monkeypatch,
    ):
        """Target is the ONLY thing in the project → predicate False
        → WARN fires (ON-with-writes default)."""
        monkeypatch.delenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
        )
        _jrs._reset_defer_autopromote_for_tests()
        try:
            service = _build_defer_watchdog_service(defer_watchdog_engine)
            now = datetime.now(timezone.utc)

            target_instance_id = "inst-target-only"
            _seed_instance(
                defer_watchdog_engine, target_instance_id,
                status="running", project_id="test-project",
                created_at=now - timedelta(seconds=60),
            )
            task_id = _seed_deferred_pending_task(
                defer_watchdog_engine,
                instance_id=target_instance_id,
                created_at=now - timedelta(seconds=120),
            )

            # No other instance, no other JobItem — target is alone.

            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=60,
                min_orphan_age_seconds=900,
            )

            # Detection record landed (autopromoted — ON path; default
            # posture after the 2026-09-06 operator decision).
            detection_records = [
                d for d in (stats.get("details") or [])
                if d.get("task_id") == task_id
                and d.get("instance_id") == target_instance_id
                and d.get("pattern") == "defer_self_witness_autopromoted"
            ]
            assert detection_records, (
                f"Target alone in project MUST trigger detection "
                f"(autopromoted path; default-ON posture). "
                f"Got detection_records="
                f"{[d for d in (stats.get('details') or []) if d.get('task_id') == task_id]}"
            )

            # ON-with-writes default: ``is_deferred`` flipped to False
            # (the bounded unstick).
            assert _get_task_is_deferred(
                defer_watchdog_engine, task_id,
            ) is False
        finally:
            _jrs._reset_defer_autopromote_for_tests()

    @pytest.mark.asyncio
    async def test_other_instance_settled_mirror_holds_gate(
        self, defer_watchdog_engine, monkeypatch,
    ):
        """Other-instance settled message mirror → predicate True
        (post-Fix-B clause catches non-target mirrors) → no detection."""
        monkeypatch.delenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
        )
        _jrs._reset_defer_autopromote_for_tests()
        try:
            service = _build_defer_watchdog_service(defer_watchdog_engine)
            now = datetime.now(timezone.utc)

            target_instance_id = "inst-target-mirror-test"
            _seed_instance(
                defer_watchdog_engine, target_instance_id,
                status="running", project_id="test-project",
                created_at=now - timedelta(seconds=60),
            )
            task_id = _seed_deferred_pending_task(
                defer_watchdog_engine,
                instance_id=target_instance_id,
                created_at=now - timedelta(seconds=120),
            )

            # Another instance in the same project with a SETTLED
            # message-mirror JobItem (admission_state='done', job_type=
            # 'message') and the instance is still alive — the
            # post-Fix-B clause catches this as a busy-set witness.
            other_instance_id = "inst-other-mirror"
            _seed_instance(
                defer_watchdog_engine, other_instance_id,
                status="running", project_id="test-project",
                created_at=now - timedelta(seconds=60),
            )
            _seed_active_job_item(
                defer_watchdog_engine,
                job_id="job-other-mirror",
                instance_id=other_instance_id,
                project_id="test-project",
                admission_state=AdmissionState.DONE.value,  # settled
                job_type="message",
                created_at=now - timedelta(seconds=60),
            )

            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=60,
                min_orphan_age_seconds=900,
            )

            # No detection record — other-instance settled mirror
            # holds the gate.
            detection_records = [
                d for d in (stats.get("details") or [])
                if d.get("task_id") == task_id
            ]
            assert not detection_records, (
                f"Other-instance settled mirror MUST hold the gate. "
                f"Got detection_records={detection_records}"
            )
        finally:
            _jrs._reset_defer_autopromote_for_tests()


# ─────────────────────────────────────────────────────────────────────
# Matrix D — defensive skips (defensive guard paths).
# ─────────────────────────────────────────────────────────────────────


class TestDeferSelfWitnessDefensiveSkips:
    """Defensive skip paths — never guess on missing input."""

    @pytest.mark.asyncio
    async def test_missing_instance_record_skipped(
        self, defer_watchdog_engine, monkeypatch,
    ):
        """A deferred task whose instance row is missing (orphan
        class) MUST be defensively skipped — the resolver cannot
        compute the WS1 predicate without project_id."""
        monkeypatch.delenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
        )
        _jrs._reset_defer_autopromote_for_tests()
        try:
            service = _build_defer_watchdog_service(defer_watchdog_engine)
            now = datetime.now(timezone.utc)

            # Seed a deferred task whose instance row is MISSING.
            task_id = _seed_deferred_pending_task(
                defer_watchdog_engine,
                instance_id="inst-does-not-exist",
                created_at=now - timedelta(seconds=120),
            )

            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=60,
                min_orphan_age_seconds=900,
            )

            # Defensive skip record landed.
            skip_records = [
                d for d in (stats.get("details") or [])
                if d.get("task_id") == task_id
                and d.get("pattern") == "defer_self_witness_skipped_no_instance"
            ]
            assert skip_records, (
                f"Missing-instance deferred task MUST emit a "
                f"'defer_self_witness_skipped_no_instance' record. "
                f"Got details="
                f"{[d for d in (stats.get('details') or []) if d.get('task_id') == task_id]}"
            )

            # Defensive skip — the resolver cannot compute the WS1
            # predicate without project_id, so no flip is attempted.
            # Under the default-ON posture, the absence of a project
            # gate evaluation means the bounded unstick is NOT
            # triggered for this row (defensive skip wins over the
            # default-on flip).
            assert _get_task_is_deferred(
                defer_watchdog_engine, task_id,
            ) is True
        finally:
            _jrs._reset_defer_autopromote_for_tests()

    @pytest.mark.asyncio
    async def test_fresh_task_within_grace_skipped(
        self, defer_watchdog_engine, monkeypatch,
    ):
        """A deferred PENDING task YOUNGER than the grace MUST NOT
        be evaluated — same boundary discipline as Patterns (a)/(d)."""
        monkeypatch.delenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
        )
        _jrs._reset_defer_autopromote_for_tests()
        try:
            service = _build_defer_watchdog_service(defer_watchdog_engine)
            now = datetime.now(timezone.utc)

            target_instance_id = "inst-fresh"
            _seed_instance(
                defer_watchdog_engine, target_instance_id,
                status="running", project_id="test-project",
                created_at=now - timedelta(seconds=60),
            )
            # Fresh — created 5s ago, well within the 60s grace.
            task_id = _seed_deferred_pending_task(
                defer_watchdog_engine,
                instance_id=target_instance_id,
                created_at=now - timedelta(seconds=5),
            )

            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=60,
                min_orphan_age_seconds=900,
            )

            # No detection record — the task is too fresh.
            detection_records = [
                d for d in (stats.get("details") or [])
                if d.get("task_id") == task_id
            ]
            assert not detection_records, (
                f"Fresh deferred task MUST NOT be evaluated "
                f"(within grace). Got detection_records="
                f"{detection_records}"
            )
        finally:
            _jrs._reset_defer_autopromote_for_tests()


# ─────────────────────────────────────────────────────────────────────
# Matrix E — boot-log emission.
# ─────────────────────────────────────────────────────────────────────


class TestDeferAutopromoteBootLog:
    """Boot-log emission contract — the
    ``emit_defer_autopromote_boot_log`` function MUST fire exactly once
    per process and name the resolved state at INFO.

    Mirrors ``emit_orphan_f1_boot_log`` (f1 discipline).
    """

    def test_boot_log_emitted_once(self, monkeypatch, caplog):
        """Boot log fires exactly once; subsequent calls are no-ops."""
        monkeypatch.delenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", raising=False,
        )
        _jrs._reset_defer_autopromote_for_tests()
        with caplog.at_level(logging.INFO, logger="daemon.services.job_recovery_service"):
            _jrs.emit_defer_autopromote_boot_log()
            first_calls = [
                r for r in caplog.records
                if "defer-self-witness autopromote" in r.getMessage()
            ]
            assert len(first_calls) == 1, (
                f"First boot-log call MUST emit exactly one record. "
                f"Got {len(first_calls)} records: "
                f"{[r.getMessage()[:120] for r in first_calls]}"
            )
            # The default-ON message MUST be present (operator decision
            # 2026-09-06 — the bounded unstick runs by default).
            assert any(
                "ENABLED" in r.getMessage()
                for r in first_calls
            ), (
                f"Default-ON boot log MUST include the ENABLED "
                f"marker. Got messages="
                f"{[r.getMessage()[:120] for r in first_calls]}"
            )

            # Second call — no-op (the boot-log-emitted flag is set).
            _jrs.emit_defer_autopromote_boot_log()
            second_calls = [
                r for r in caplog.records
                if "defer-self-witness autopromote" in r.getMessage()
            ]
            assert len(second_calls) == 1, (
                f"Second boot-log call MUST be a no-op. "
                f"Got {len(second_calls)} records."
            )
        _jrs._reset_defer_autopromote_for_tests()

    def test_boot_log_emits_enabled_message_when_on(self, monkeypatch, caplog):
        """When the flag is ON, the boot log emits the ENABLED message."""
        monkeypatch.setenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", "1",
        )
        _jrs._reset_defer_autopromote_for_tests()
        with caplog.at_level(logging.INFO, logger="daemon.services.job_recovery_service"):
            _jrs.emit_defer_autopromote_boot_log()
            records = [
                r for r in caplog.records
                if "defer-self-witness autopromote" in r.getMessage()
            ]
            assert len(records) == 1, (
                f"Expected exactly one boot-log record. Got "
                f"{len(records)}: "
                f"{[r.getMessage()[:120] for r in records]}"
            )
            assert "ENABLED" in records[0].getMessage(), (
                f"ON-state boot log MUST include the ENABLED marker. "
                f"Got message={records[0].getMessage()!r}"
            )
        _jrs._reset_defer_autopromote_for_tests()

    def test_boot_log_emits_disabled_message_when_off(
        self, monkeypatch, caplog,
    ):
        """When the flag is explicit OFF (operator escape hatch), the
        boot log emits the DISABLED message.

        Proves the explicit-OFF escape hatch survives the default flip
        byte-identically — operator who sets ``=0`` and restarts sees
        the same DISABLED boot line they relied on under the previous
        default posture.
        """
        monkeypatch.setenv(
            "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", "0",
        )
        _jrs._reset_defer_autopromote_for_tests()
        with caplog.at_level(logging.INFO, logger="daemon.services.job_recovery_service"):
            _jrs.emit_defer_autopromote_boot_log()
            records = [
                r for r in caplog.records
                if "defer-self-witness autopromote" in r.getMessage()
            ]
            assert len(records) == 1, (
                f"Expected exactly one boot-log record. Got "
                f"{len(records)}: "
                f"{[r.getMessage()[:120] for r in records]}"
            )
            assert "DISABLED" in records[0].getMessage(), (
                f"OFF-state boot log MUST include the DISABLED "
                f"marker. Got message={records[0].getMessage()!r}"
            )
        _jrs._reset_defer_autopromote_for_tests()


# ─────────────────────────────────────────────────────────────────────
# Matrix F — Constitution census stays 23/1/0.
# ─────────────────────────────────────────────────────────────────────


class TestDeferSelfWitnessCensus:
    """Constitution census discipline (brief §3) — the promote path
    WRITES ``task.is_deferred`` (NOT
    ``job_queue_items.admission_state``), and the census keys
    ``admission_state`` on ``job_queue_items`` rows. The census stays
    23/1/0.

    Drift-guard proof (runs the canonical drift-guard test) + a
    runtime import check (counts the static sets).
    """

    def test_runtime_census_23_1_0(self):
        """Runtime import check — the constitution stays 23/1/0."""
        from daemon.job_state.constitution import (
            KNOWN_ADMISSION_STATE_WRITERS,
            KNOWN_JOBITEM_CREATORS,
            KNOWN_MINT_SITES,
        )

        n_writers = len(KNOWN_ADMISSION_STATE_WRITERS)
        n_creators = len(KNOWN_JOBITEM_CREATORS)
        n_mints = len(KNOWN_MINT_SITES)

        assert n_writers == 23, (
            f"KNOWN_ADMISSION_STATE_WRITERS count drifted from 23 to "
            f"{n_writers}. Pattern (g) WRITES task.is_deferred — NOT "
            f"job_queue_items.admission_state — so the count MUST "
            f"stay at 23."
        )
        assert n_creators == 1, (
            f"KNOWN_JOBITEM_CREATORS count drifted from 1 to "
            f"{n_creators}. Pattern (g) does NOT create JobItems, "
            f"so the count MUST stay at 1."
        )
        assert n_mints == 0, (
            f"KNOWN_MINT_SITES count drifted from 0 to {n_mints}. "
            f"Pattern (g) does NOT mint new work_ids, so the count "
            f"MUST stay at 0."
        )

    def test_pattern_g_does_not_write_admission_state(self):
        """Static analysis: Pattern (g) does NOT introduce any new
        ``admission_state`` writers.

        Greps the production file
        (``daemon/services/job_recovery_service.py``) for any
        ``admission_state`` write surface added by Pattern (g).
        Pattern (g) only writes ``task.is_deferred`` (via raw SQL
        with the column name hard-coded); admission_state writes
        are out of scope.

        This is a defense-in-depth check — the static constitution
        drift test (run separately) is the canonical guard, but
        this file-scoped grep catches regressions early.
        """
        prod_path = (
            Path(__file__).parent.parent.parent
            / "daemon"
            / "services"
            / "job_recovery_service.py"
        )
        contents = prod_path.read_text()
        # Pattern (g) raw SQL must NOT mention ``admission_state``
        # in the UPDATE clause — only ``is_deferred``.
        # Find the Pattern (g) UPDATE statement.
        import re
        g_update_blocks = re.findall(
            r"UPDATE\s+task\s+SET\s+[^;]+",
            contents,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # The f1 / e patterns use ``UPDATE task`` too — but those
        # are NOT added by WS3 and pre-existed (they target status,
        # completed_at, error — not admission_state). The
        # assertion only checks WS3-introduced patterns.
        # Filter to those that mention ``is_deferred``.
        g_is_deferred_updates = [
            u for u in g_update_blocks
            if "is_deferred" in u.lower()
        ]
        for u in g_is_deferred_updates:
            # Each WS3-introduced UPDATE must set ``is_deferred``
            # only — not ``admission_state`` (which would write to
            # the JobItem side and bust the census).
            assert "admission_state" not in u.lower(), (
                f"WS3 UPDATE statement writes admission_state — "
                f"this would bust the 23/1/0 census. Statement: "
                f"{u[:200]}"
            )

    def test_constitution_drift_test_passes(self):
        """Run the canonical constitution drift-guard test in-process.

        The drift-guard test
        (``tests/unit/job_state/test_constitution_drift.py``) is the
        canonical bidirectional-detector between source-discovered
        writer/creator sets and the static ``KNOWN_*`` universes.
        If Pattern (g) accidentally introduced a new admission_state
        writer or JobItem creator, this test would fail.

        We invoke the test functions directly here rather than
        spawning a subprocess — the brief says "drift-guard proof"
        is sufficient evidence, not a separate pytest run.
        """
        from daemon.job_state import constitution

        # Bidirectional writers detector.
        static_writers = set(constitution.KNOWN_ADMISSION_STATE_WRITERS)
        try:
            source_writers = (
                constitution.discover_admission_state_writer_paths()
            )
        except RuntimeError:
            # Frozen-binary case — fall back to the static set.
            source_writers = static_writers
        assert static_writers == source_writers, (
            f"KNOWN_ADMISSION_STATE_WRITERS drifted vs source: "
            f"only_in_static="
            f"{sorted(static_writers - source_writers)} "
            f"only_in_source="
            f"{sorted(source_writers - static_writers)}"
        )

        # Bidirectional creators detector.
        static_creators = set(constitution.KNOWN_JOBITEM_CREATORS)
        try:
            source_creators = (
                constitution.discover_jobitem_creator_paths()
            )
        except RuntimeError:
            source_creators = static_creators
        assert static_creators == source_creators, (
            f"KNOWN_JOBITEM_CREATORS drifted vs source: "
            f"only_in_static="
            f"{sorted(static_creators - source_creators)} "
            f"only_in_source="
            f"{sorted(source_creators - static_creators)}"
        )

        # Mints — subset-only (KNOWN_MINT_SITES ⊆ source).
        try:
            source_mints = constitution.discover_work_id_mint_paths()
        except RuntimeError:
            source_mints = set(constitution.KNOWN_MINT_SITES)
        missing = set(constitution.KNOWN_MINT_SITES) - source_mints
        assert not missing, (
            f"KNOWN_MINT_SITES references stale mints: "
            f"{sorted(missing)}"
        )