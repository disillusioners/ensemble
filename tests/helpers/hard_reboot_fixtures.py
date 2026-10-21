"""Shared fixtures for the hard-reboot recovery test packs.

Feature: hard-reboot recovery. Phases 1-2.

Single source for the harness boilerplate the two new packs share —
the house pattern (``tests/helpers/`` already holds 14 shared modules;
byte-copied harness trios drift, see the repo blueprint's testing
contract):

  * ``tests/unit/test_message_queue_boot_recovery.py`` — the boot-pass
    pack (SQLite harness + row seeds).
  * ``tests/unit/test_connect_timeout.py`` — the PG-connect budget
    pack (PG-coordinate neutraliser + fake PG config).

Contents:

  * :data:`BOOT_EPOCH` — the fixed boot instant every "before the
    boot" predicate is written against.
  * :func:`engine` / :func:`repo` — REAL file-backed SQLite fixtures
    (NullPool + per-connection PRAGMAs; NOT ``:memory:`` — the boot
    pass opens more than one connection and ``:memory:`` is
    per-connection).
  * :func:`seed_instance` / :func:`seed_message` / :func:`seed_task` /
    :func:`seed_report_injection` / :func:`message_status` — the row
    seeds.

Importable from both packs via the ``tests.helpers`` package (the
established pattern — see ``tests.helpers.unified_spawn_fixtures``).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401 — register
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401 — register
import daemon.repositories.task.models  # noqa: F401
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.repositories.instance.models import Instance
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskType

# The boot epoch — a fixed instant, so "before the boot" is exact.
BOOT_EPOCH = datetime(2026, 10, 21, 6, 0, 0)

# The ambient shell may export the LIVE PG coordinates; the factory
# resolves POSTGRES_* with precedence over the config, so a
# prod-pointing env would silently redirect the constructed engine.
PG_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)


@pytest.fixture
def engine(tmp_path):
    """REAL SQLite FILE database, per-connection PRAGMAs, NullPool."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/mq_boot_recovery.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    # Only the four tables this work touches — a full create_all() of
    # every registered model costs ~0.5s per test. The
    # ``report_injections`` table is here for the D19 lane-6
    # composition test (the RDRS lane-6 predicate joins it).
    SQLModel.metadata.create_all(
        eng,
        tables=[
            MessageQueue.__table__,
            Instance.__table__,
            Task.__table__,
            ReportInjection.__table__,
        ],
    )
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    return SQLModelMessageQueueRepository(engine)


@pytest.fixture
def no_pg_env(monkeypatch):
    """Neutralize ambient POSTGRES_* env so the factory uses the config."""
    for var in PG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def pg_config() -> EnsembleConfig:
    """Config pointing at a clearly-fake test coordinate (no connect)."""
    return EnsembleConfig(
        database="postgres",
        postgres=PostgresConfig(
            host="127.0.0.1",
            port=15432,
            db="ensemble_test",
            user="ensemble",
            password="ensemble_dev",
        ),
    )


def seed_instance(eng: Engine, instance_id: str, status: str) -> None:
    with Session(eng) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                status=status,
            )
        )
        s.commit()


def seed_message(
    eng: Engine,
    message_id: str,
    instance_id: str,
    *,
    last_activity_at: datetime | None,
    status: str = MessageStatus.PROCESSING.value,
    source: str | None = None,
) -> None:
    with Session(eng) as s:
        s.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="fixture content",
                status=status,
                source=source,
                last_activity_at=last_activity_at,
                processing_started_at=last_activity_at,
            )
        )
        s.commit()


def seed_task(
    eng: Engine,
    work_id: str,
    instance_id: str,
    message_id: str,
    status: str,
    *,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    last_heartbeat_at: datetime | None = None,
) -> None:
    with Session(eng) as s:
        s.add(
            Task(
                work_id=work_id,
                instance_id=instance_id,
                message_id=message_id,
                status=status,
                task_type=task_type,
                last_heartbeat_at=last_heartbeat_at,
            )
        )
        s.commit()


def seed_report_injection(
    eng: Engine,
    *,
    injection_id: str,
    parent_instance_id: str,
    child_instance_id: str,
    child_message_id: str,
    content: str,
) -> None:
    """Seed a PENDING report-injection obligation marker.

    The marker is one half of the RDRS lane-6 candidate shape (the
    other half is the ``ready`` wake row + the ``running`` wake task).
    """
    with Session(eng) as s:
        s.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_instance_id,
                child_instance_id=child_instance_id,
                child_message_id=child_message_id,
                content=content,
                state=ReportInjectionState.PENDING.value,
            )
        )
        s.commit()


def message_status(eng: Engine, message_id: str) -> str:
    with Session(eng) as s:
        row = s.get(MessageQueue, message_id)
        return row.status
