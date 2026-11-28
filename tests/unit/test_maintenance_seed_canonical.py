"""PIN — the maintenance seeder writes CANONICAL rows (schedule listing
truth, Phase 1 — architecture v3 §4.3, work item W6).

The rules this file pins:

* a fresh seed writes a CANONICAL recurring row — ``recurrence=weekly``
  + a raw cron expression + the local time + the timezone — with NO
  ``chain`` block (the chain marker is demoted to an internal
  compatibility representation, readable for legacy rows but never
  composed for new ones) and NO ``run_at`` (a recurring row re-arms
  itself through its cron);
* the canonical row is written under a DISTINCT label (the chain label
  stays free for the legacy chain row's history — the dead-label reuse
  never arises) under the deterministic uuid5 identity, so the next
  boot is a no-op on the SAME row;
* the archive-aware boot guard rule is PRESERVED — an archived
  CANCELLED chain is NEVER revived and NOTHING is created in its place.

MUTATION-VERIFIED (skill ``mutation-verified-regression-pins``): the
"seed reverted to the chain shape" mutation turns this pin RED; the
restore turns it GREEN again.

SAFETY FENCE: a throwaway SQLite database under ``tmp_path`` — no
PostgreSQL, no daemon, no live contact, no network.  The artifact root
is redirected into ``tmp_path`` so the LIVE-INSTALL tree is never
touched.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, select

from daemon.models.board import BoardItem, BoardItemTag
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
    ProjectTagLink,
)
from daemon.repositories.source.models import (
    ScheduleExecution,
    SourceConfig,
    SourceConfigArchive,
    SourceStatus,
)
from daemon.repositories.source.maintenance_seed import (
    CANONICAL_SEED_NAME,
    CANONICAL_SEED_SOURCE_ID,
    CHAIN_MODE,
    SEED_NAME,
    SEED_SOURCE_ID,
    SEED_SOURCE_TYPE,
    WEEKLY_RUN_MESSAGE,
    seed_central_maintenance_schedule,
)

GUARD_TABLES = [
    SourceConfig.__table__,
    SourceConfigArchive.__table__,
    ScheduleExecution.__table__,
    Project.__table__,
    ProjectTagLink.__table__,
    ProjectShortnameLink.__table__,
    ProjectMetadataRecord.__table__,
    BoardItem.__table__,
    BoardItemTag.__table__,
]


@pytest.fixture
def guard_engine(tmp_path) -> Engine:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'canonical_seed.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(engine, tables=GUARD_TABLES)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def results_root(tmp_path) -> Path:
    return tmp_path / "live-install"


def _rows(engine: Engine) -> list[SourceConfig]:
    with Session(engine) as session:
        return list(session.exec(select(SourceConfig)))


def _row(engine: Engine, source_id: str) -> SourceConfig | None:
    with Session(engine) as session:
        return session.get(SourceConfig, source_id)


def _insert_archived_cancelled_chain(engine: Engine) -> None:
    """Archive the chain row in its CANCELLED shape (BR-3)."""
    now = datetime.now(timezone.utc).isoformat()
    config = {
        "agent": "maintenancer",
        "message": WEEKLY_RUN_MESSAGE,
        "timezone": "Asia/Ho_Chi_Minh",
        "recurrence": "once",
        "local_time": "03:00",
        "run_at": now,
        "instance_mode": "new_instance",
        "project_id": "system-default",
        "chain": {
            "mode": CHAIN_MODE,
            "window_start": "01:00",
            "window_end": "05:00",
            "weekday": 0,
            "timezone": "Asia/Ho_Chi_Minh",
            "run_at_utc": now,
        },
    }
    with Session(engine) as session:
        session.add(
            SourceConfigArchive(
                source_id=SEED_SOURCE_ID,
                source_type=SEED_SOURCE_TYPE,
                name=SEED_NAME,
                config=json.dumps(config),
                enabled=False,
                autostart=True,
                status=SourceStatus.CANCELLED.value,
                error_message=None,
                created_at=now,
                updated_at=now,
                archived_at=now,
            )
        )
        session.commit()


class TestCanonicalSeed:
    def test_a_fresh_seed_writes_a_canonical_recurring_row(
        self, guard_engine, results_root
    ):
        """No row, no archive ⇒ a CANONICAL row: no chain block, a cron
        expression, the fixed window-start slot, a DISTINCT label."""
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "created"
        row = _row(guard_engine, CANONICAL_SEED_SOURCE_ID)
        assert row is not None
        config = row.config
        # The canonical recurring shape.
        assert config["recurrence"] == "weekly"
        assert config["schedule"] == "0 1 * * 0"  # window start, weekday 0
        assert config["local_time"] == "01:00"
        assert config["timezone"] == "Asia/Ho_Chi_Minh"
        # NO chain block, NO run_at.
        assert "chain" not in config
        assert "run_at" not in config
        # The task itself is unchanged — same agent, same message.
        assert config["agent"] == "maintenancer"
        assert config["message"] == WEEKLY_RUN_MESSAGE
        # The DISTINCT label — the chain label stays free for the legacy
        # chain row's history.
        assert row.name == CANONICAL_SEED_NAME
        assert CANONICAL_SEED_NAME != SEED_NAME
        assert _row(guard_engine, SEED_SOURCE_ID) is None

    def test_the_canonical_identity_is_deterministic_and_idempotent(
        self, guard_engine, results_root
    ):
        """The deterministic uuid5 identity makes the next boot a no-op on
        the SAME row (no second row, no re-create)."""
        assert CANONICAL_SEED_SOURCE_ID == str(
            uuid.uuid5(uuid.NAMESPACE_DNS, CANONICAL_SEED_NAME)
        )
        first = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert first.action == "created"
        second = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert second.action == "noop_existing"
        assert len(_rows(guard_engine)) == 1

    def test_an_archived_cancelled_chain_is_never_revived(
        self, guard_engine, results_root
    ):
        """The archive-aware rule is PRESERVED: an archived CANCELLED
        chain is never revived and NOTHING is created in its place."""
        _insert_archived_cancelled_chain(guard_engine)
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "noop_archived_cancelled"
        # NOTHING was created — not a chain row, not a canonical row.
        assert _rows(guard_engine) == []
        assert _row(guard_engine, SEED_SOURCE_ID) is None
        assert _row(guard_engine, CANONICAL_SEED_SOURCE_ID) is None
