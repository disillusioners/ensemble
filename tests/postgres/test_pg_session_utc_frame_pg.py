"""PG-session UTC-frame pins for the B2/B3 engine fix (tz convention).

Feature ``feature/fix-job-queue-timestamps-tz`` (review follow-up).
``create_postgres_engine`` now carries libpq ``options=-c timezone=UTC``
(``PG_SESSION_CONNECT_ARGS`` in ``daemon/repositories/factory.py``), so
every pooled PG session renders ``now()`` in UTC — aligning the SQL-side
age readers with the naive-UTC digit writers:

* B2 — readiness heartbeat max-age
  (``daemon/services/readiness.py``, 120s threshold):
  without the fix a +07 session inflates the age ~7h → /readyz
  permanently degraded from the first post-activation heartbeat.
* B3 — hung-children watchdog
  (``daemon/repositories/instance/repository.py::
  _build_hung_children_sql``, 3600s threshold):
  without the fix every non-terminal child is falsely flagged hung
  → auto-revive storm risk.

Adversarial setup: the fixture runs ``ALTER DATABASE <test-db> SET
timezone TO 'Asia/Ho_Chi_Minh'`` so NEW connections default to a +07
session — exactly the production condition (independent of the
workstation's cluster default). The control engine (plain
``create_engine`` on the same coordinates, no options) then proves the
adversarial precondition, while the factory engine must render UTC.
The database-level setting is reset on teardown. This mutates ONLY the
disposable test database the conftest already creates/drops schema in —
never a live database.

Marker: ``postgres`` (auto-applied by ``tests/postgres/conftest.py``);
run serially:
``pytest tests/postgres/ --override-ini="addopts=" -m postgres``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.instance.models  # noqa: F401 — register Instance
import daemon.repositories.task.models  # noqa: F401 — register Task
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.repositories.factory import create_postgres_engine
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.services.readiness import evaluate_queue_freshness, make_queue_probe
from daemon.services.timestamps import now_utc_naive
from daemon.repositories.task.models import TaskStatus, TaskType

# Mirror the conftest's PG_TEST_* resolution (same defaults) so the
# factory-built engine and the conftest schema share coordinates.
PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
PG_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"

_ADVERSARIAL_TZ = "Asia/Ho_Chi_Minh"

# POSTGRES_* must NEVER leak into the factory: create_postgres_engine
# resolves coordinates with os.environ.get("POSTGRES_*", cfg)
# precedence, and a prod-pointing shell would redirect the engine.
_PG_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)


@pytest.fixture
def utc_frame(monkeypatch):
    """Adversarial DB-default tz + scrubbed POSTGRES_* env + factory config.

    Returns the ``EnsembleConfig`` the factory engine will be built
    from. The ALTER DATABASE takes effect for connections opened AFTER
    it runs — engines created inside the test are fresh, so they see
    the adversarial default.
    """
    for var in _PG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    bootstrap = create_engine(PG_URL, pool_pre_ping=True)
    with bootstrap.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(
            text(f"ALTER DATABASE {PG_DB} SET timezone TO '{_ADVERSARIAL_TZ}'")
        )
    bootstrap.dispose()

    yield EnsembleConfig(
        database="postgres",
        postgres=PostgresConfig(
            host=PG_HOST,
            port=PG_PORT,
            db=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
        ),
    )

    restore = create_engine(PG_URL, pool_pre_ping=True)
    with restore.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(f"ALTER DATABASE {PG_DB} RESET timezone"))
    restore.dispose()


def _control_engine() -> Engine:
    """Engine WITHOUT the UTC session options — the pre-fix shape."""
    return create_engine(PG_URL, pool_pre_ping=True)


def _clear_tasks(conn) -> None:
    conn.execute(
        text("DELETE FROM task WHERE instance_id = 'utc-frame-inst'")
    )


def _clear_instances(conn) -> None:
    conn.execute(
        text(
            "DELETE FROM instances WHERE instance_id IN"
            " ('utc-frame-parent', 'utc-frame-child')"
        )
    )


def test_factory_engine_sessions_render_utc_clock(pg_engine, utc_frame):
    """Through the ENGINE: SHOW timezone = UTC and now() renders +00 —
    on repeated pooled checkouts — while a plain engine on the same
    coordinates keeps the adversarial non-UTC default."""
    control = _control_engine()
    fixed = create_postgres_engine(utc_frame)
    try:
        with control.connect() as conn:
            control_tz = conn.execute(text("SHOW timezone")).scalar()
        assert control_tz != "UTC", (
            "adversarial precondition broken: the test DB default is "
            f"UTC ({control_tz!r}) — the control engine cannot "
            "demonstrate the pre-fix frame skew"
        )

        for _checkout in range(2):  # pooled checkout, return, re-checkout
            with fixed.connect() as conn:
                assert conn.execute(text("SHOW timezone")).scalar() == "UTC"
                now_txt = conn.execute(text("SELECT now()::text")).scalar()
                assert now_txt.endswith("+00"), now_txt
    finally:
        control.dispose()
        fixed.dispose()


def test_readiness_heartbeat_age_is_frame_aligned(pg_engine, utc_frame):
    """B2: a RUNNING task whose last_heartbeat_at carries naive-UTC
    digits (post-fix writer shape) reads FRESH (< 120s) through the
    factory engine; the control (pre-fix) engine reads the same row
    ~7h old — the /readyz permanent-degradation shape."""
    with pg_engine.begin() as conn:
        _clear_tasks(conn)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute(
            text(
                """
                INSERT INTO task (task_type, instance_id, message_id,
                                  status, retry_count, created_at,
                                  cancel_requested, retry_scheduled,
                                  work_id, is_deferred, is_background,
                                  worker_id, started_at,
                                  last_heartbeat_at)
                VALUES (:task_type, 'utc-frame-inst', NULL, :status, 0,
                        :created_at, false, false, :work_id, false,
                        false, NULL, :created_at, :heartbeat)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "status": TaskStatus.RUNNING.value,
                "created_at": now,
                "work_id": "utc-frame-heartbeat",
                "heartbeat": now_utc_naive(),  # naive-UTC digits
            },
        )

    control = _control_engine()
    fixed = create_postgres_engine(utc_frame)
    try:
        control_age = make_queue_probe(control)()
        fixed_age = make_queue_probe(fixed)()

        # Fixed engine: same frame as the writer → fresh.
        assert fixed_age is not None
        assert fixed_age < 120, fixed_age
        fresh, _ = evaluate_queue_freshness(
            fixed_age, threshold_seconds=120
        )
        assert fresh is True

        # Control engine: +07 session frame vs UTC digits → ~7h skew.
        assert control_age is not None
        assert control_age > 6 * 3600, control_age
        control_fresh, _ = evaluate_queue_freshness(
            control_age, threshold_seconds=120
        )
        assert control_fresh is False
    finally:
        control.dispose()
        fixed.dispose()
        with pg_engine.begin() as conn:
            _clear_tasks(conn)


def test_hung_children_age_is_frame_aligned(pg_engine, utc_frame):
    """B3: a live child whose last_activity_at carries naive-UTC digits
    is NOT hung through the factory engine; the control (pre-fix)
    engine flags it ~7h old — the auto-revive-storm shape. Executes the
    real ``_build_hung_children_sql`` predicate."""
    activity = now_utc_naive()
    with Session(pg_engine) as session:
        for iid, parent in (
            ("utc-frame-parent", None),
            ("utc-frame-child", "utc-frame-parent"),
        ):
            session.add(
                Instance(
                    instance_id=iid,
                    agent_id="developer",
                    agent_dir="/tmp/agents/developer",
                    agent_name="developer",
                    project_id="tz-frame-project",
                    status="running",
                    parent_id=parent,
                    last_activity_at=activity if parent else None,
                )
            )
        session.commit()

    sql = SQLModelInstanceRepository._build_hung_children_sql("postgresql")
    binds = {"parent_id": "utc-frame-parent", "threshold_seconds": 3600}

    control = _control_engine()
    fixed = create_postgres_engine(utc_frame)
    try:
        with fixed.connect() as conn:
            fixed_rows = conn.execute(sql, binds).fetchall()
        assert fixed_rows == [], fixed_rows

        with control.connect() as conn:
            control_rows = conn.execute(sql, binds).fetchall()
        assert len(control_rows) == 1, control_rows
        assert control_rows[0][0] == "utc-frame-child"
        assert control_rows[0][1] > 6 * 3600, control_rows
    finally:
        control.dispose()
        fixed.dispose()
        with pg_engine.begin() as conn:
            _clear_instances(conn)
