"""Central Maintenance System (P1) — the boot-time chain guard.

Covers plan ``phase1-plan.md`` Task 1.1 (the seeder) and Task 1.4
(the archive-aware guard): BR-1 (the past-``run_at`` re-mint
predicate), BR-3 (the archive-aware recreate), the BR-15 reportless-week
detector and the first-boot artifact-root creation.

SAFETY FENCE: a throwaway SQLite database under ``tmp_path`` — no
PostgreSQL, no daemon, no live contact, no network.  The artifact root
is redirected into ``tmp_path`` so the LIVE-INSTALL tree is never
touched.
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    InstanceMapping,
    ProcessedMessage,
    ScheduleExecution,
    ScheduleExecutionArchive,
    SourceConfig,
    SourceConfigArchive,
    SourceStatus,
)
from daemon.repositories.source.maintenance_seed import (
    CANONICAL_SEED_NAME,
    CANONICAL_SEED_SOURCE_ID,
    CHAIN_MODE,
    FINDING_KIND_RESULTS_ROOT_EMPTY,
    SEED_NAME,
    SEED_SOURCE_ID,
    SEED_SOURCE_TYPE,
    WEEKLY_RUN_MESSAGE,
    detect_reportless_week,
    detect_results_root_empty,
    ensure_maintenance_results_dir,
    maintenance_results_dir,
    resolve_live_install_root,
    seed_central_maintenance_schedule,
)

CAP_ENV = "ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS"

GUARD_TABLES = [
    SourceConfig.__table__,
    SourceConfigArchive.__table__,
    ScheduleExecution.__table__,
    Project.__table__,
    ProjectTagLink.__table__,
    ProjectShortnameLink.__table__,
    ProjectMetadataRecord.__table__,
    # The board tables — the D4 fresh-root guard writes its ONE
    # de-duplicated notice here (the durable de-dup surface).
    BoardItem.__table__,
    BoardItemTag.__table__,
]


@pytest.fixture
def guard_engine(tmp_path) -> Engine:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chain_guard.db'}",
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


def _chain_config(run_at: datetime, **overrides) -> dict:
    config = {
        "agent": "maintenancer",
        "message": WEEKLY_RUN_MESSAGE,
        "timezone": "Asia/Ho_Chi_Minh",
        "recurrence": "once",
        "local_time": "03:00",
        "run_at": run_at.isoformat(),
        "instance_mode": "new_instance",
        "project_id": "system-default",
        "chain": {
            "mode": CHAIN_MODE,
            "window_start": "01:00",
            "window_end": "05:00",
            "weekday": 0,
            "timezone": "Asia/Ho_Chi_Minh",
            "run_at_utc": run_at.isoformat(),
        },
    }
    config.update(overrides)
    return config


def _insert_row(
    engine: Engine,
    *,
    source_id: str = SEED_SOURCE_ID,
    name: str = SEED_NAME,
    config: dict | None = None,
    enabled: bool = True,
    status: str = SourceStatus.RUNNING.value,
) -> SourceConfig:
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        row = SourceConfig(
            source_id=source_id,
            source_type=SEED_SOURCE_TYPE,
            name=name,
            config=config if config is not None else _chain_config(
                datetime.now(timezone.utc) + timedelta(days=3)
            ),
            credentials=None,
            enabled=enabled,
            autostart=True,
            status=status,
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _insert_archive(
    engine: Engine,
    *,
    source_id: str = SEED_SOURCE_ID,
    name: str = SEED_NAME,
    config: dict | None = None,
    status: str = SourceStatus.STOPPED.value,
) -> SourceConfigArchive:
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        row = SourceConfigArchive(
            source_id=source_id,
            source_type=SEED_SOURCE_TYPE,
            name=name,
            config=json.dumps(config if config is not None else {}),
            enabled=False,
            autostart=True,
            status=status,
            error_message=None,
            created_at=now,
            updated_at=now,
            archived_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


# ══════════════════════════════════════════════════════════════════════════
# Task 1.1 — the seed lifecycle
# ══════════════════════════════════════════════════════════════════════════


class TestSeedLifecycle:
    def test_seeding_twice_yields_exactly_one_row(self, guard_engine, results_root):
        first = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        second = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert first.action == "created"
        assert second.action == "noop_existing"
        rows = _rows(guard_engine)
        assert len(rows) == 1
        # The CANONICAL identity (architecture v3 §4.3): the seeder writes
        # a canonical recurring row under a DISTINCT label — the chain
        # label stays free for the legacy chain row (its history stays
        # greppable) and the dead-label reuse never arises.
        assert rows[0].source_id == CANONICAL_SEED_SOURCE_ID
        assert rows[0].name == CANONICAL_SEED_NAME
        assert rows[0].source_type == SEED_SOURCE_TYPE

    def test_the_seeded_row_is_canonical_and_carries_no_chain_block(
        self, guard_engine, results_root
    ):
        """The seeded row is CANONICAL (architecture v3 §4.3): ONE
        user-facing mechanism — ``recurrence=weekly`` + a raw cron + the
        local time + the timezone, under a DISTINCT label.

        There is NO ``chain`` block (the chain marker is demoted to an
        internal compatibility representation, readable for legacy rows
        but never composed for new ones) and NO ``run_at`` (a recurring
        row re-arms itself through its cron expression)."""
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        row = _row(guard_engine, CANONICAL_SEED_SOURCE_ID)
        assert row is not None
        config = row.config
        assert config["agent"] == "maintenancer"
        assert config["message"] == WEEKLY_RUN_MESSAGE
        assert config["timezone"] == "Asia/Ho_Chi_Minh"
        assert config["recurrence"] == "weekly"
        assert config["instance_mode"] == "new_instance"
        assert config["project_id"]
        assert "chain" not in config
        assert "run_at" not in config
        # The FIXED slot: the window START on the configured weekday
        # (jitter is refused until a recurring collision is MEASURED —
        # architecture v3 §2.2, Branch A).
        assert config["local_time"] == "01:00"
        assert config["schedule"] == "0 1 * * 0"

    def test_the_seeded_row_is_running_and_enabled(self, guard_engine, results_root):
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        row = _row(guard_engine, CANONICAL_SEED_SOURCE_ID)
        assert row is not None
        assert row.status == SourceStatus.RUNNING.value
        assert row.enabled is True
        assert row.autostart is True

    def test_the_message_literal_is_functional(self):
        """NOT a placeholder — the trigger token travels in the body and
        the orchestrator is told to parse it and pass ``trigger_type``."""
        assert "trigger: scheduled" in WEEKLY_RUN_MESSAGE
        assert "trigger_type" in WEEKLY_RUN_MESSAGE
        assert "trigger:" in WEEKLY_RUN_MESSAGE

    def test_the_artifact_root_is_created(self, guard_engine, results_root):
        target = maintenance_results_dir(results_root)
        assert not target.exists()
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        assert target.is_dir()
        # Idempotent.
        ensure_maintenance_results_dir(results_root)
        assert target.is_dir()

    def test_the_live_install_root_is_not_the_cwd(self):
        """The artifact root is resolved from the daemon package's own
        location — never from ``Path.cwd()`` (a worktree)."""
        root = resolve_live_install_root()
        assert root.is_absolute()
        if not getattr(sys, "frozen", False):
            assert (root / "daemon").is_dir()

    def test_the_live_install_root_is_promote_stable(self):
        """The D4 cure — the artifact root NEVER lives under
        ``releases/<ver>/``: a promote would MOVE the root (stranding the
        run history) and the keep-3 release eviction would DESTROY it.
        The basis is the INSTALL DIR (the ancestor that owns
        ``releases/``)."""
        root = resolve_live_install_root()
        assert root.is_absolute()
        assert "releases" not in root.parts, (
            f"the artifact-root basis is release-scoped: {root} — a promote "
            "would move it and the release eviction would destroy it"
        )

    def test_a_frozen_install_resolves_the_install_dir_not_the_release_dir(
        self, tmp_path, monkeypatch
    ):
        """The D4 cure's frozen leg — the staged binary lives at
        ``<INSTALL_DIR>/releases/<ver>/ensemble-prod``; the basis is the
        INSTALL DIR (the ancestor that owns ``releases/``), NEVER the
        release dir a promote replaces.  A regression to the OLD basis
        (``Path(sys.executable).parent``) REDS this leg."""
        install_dir = tmp_path / "agents-ensemble"
        release_dir = install_dir / "releases" / "v0.31.30"
        release_dir.mkdir(parents=True)
        binary = release_dir / "ensemble-prod"
        binary.write_text("", encoding="utf-8")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(binary), raising=False)
        resolved = resolve_live_install_root()
        assert resolved == install_dir.resolve()
        assert resolved != release_dir.resolve()
        # The artifact root hangs off the install dir — it survives the
        # promote (the release dir is what the promote replaces).
        assert maintenance_results_dir().parents[2] == install_dir.resolve()

    def test_a_source_tree_inside_a_release_dir_lifts_to_the_install_dir(
        self, tmp_path, monkeypatch
    ):
        """The source-install twin of the frozen leg — a daemon run from
        SOURCE inside ``<INSTALL_DIR>/releases/<ver>/`` must still land on
        the install dir, never on the release dir."""
        install_dir = tmp_path / "agents-ensemble"
        release_dir = install_dir / "releases" / "v0.31.30"
        release_dir.mkdir(parents=True)
        # The resolver reads its own module location — simulate a source
        # tree at <INSTALL>/releases/<ver>/daemon/repositories/source/.
        source_file = release_dir / "daemon" / "repositories" / "source" / (
            "maintenance_seed.py"
        )
        source_file.parent.mkdir(parents=True)
        source_file.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            "daemon.repositories.source.maintenance_seed.__file__", str(source_file)
        )
        resolved = resolve_live_install_root()
        assert resolved == install_dir.resolve()
        assert resolved != release_dir.resolve()


# ══════════════════════════════════════════════════════════════════════════
# BR-1 — the past-run_at re-mint predicate
# ══════════════════════════════════════════════════════════════════════════


class TestBr1PastRunAt:
    def test_with_the_cap_unset_the_past_run_at_is_untouched(
        self, guard_engine, results_root, monkeypatch
    ):
        """The default cap is UNLIMITED — the past ``run_at`` is LEFT for
        the adapter's past-due auto-fire (R-P1-8: a blanket re-mint would
        silently skip the missed week)."""
        monkeypatch.delenv(CAP_ENV, raising=False)
        past = datetime.now(timezone.utc) - timedelta(days=2)
        original = past.isoformat()
        _insert_row(guard_engine, config=_chain_config(past))
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "noop_existing"
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.config["run_at"] == original

    def test_with_the_cap_exceeded_the_run_at_is_re_minted(
        self, guard_engine, results_root, monkeypatch
    ):
        """A configured cap + lateness beyond it ⇒ the guard re-mints
        (otherwise the adapter would SKIP the fire and the chain would
        stall)."""
        monkeypatch.setenv(CAP_ENV, "3600")
        past = datetime.now(timezone.utc) - timedelta(days=2)
        original = past.isoformat()
        _insert_row(guard_engine, config=_chain_config(past))
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "reminted"
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.config["run_at"] != original
        assert row.config["chain"]["run_at_utc"] == row.config["run_at"]

    def test_within_the_cap_the_run_at_is_untouched(
        self, guard_engine, results_root, monkeypatch
    ):
        monkeypatch.setenv(CAP_ENV, "86400")
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        original = past.isoformat()
        _insert_row(guard_engine, config=_chain_config(past))
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "noop_existing"
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.config["run_at"] == original


# ══════════════════════════════════════════════════════════════════════════
# BR-3 — the archive-aware recreate
# ══════════════════════════════════════════════════════════════════════════


class TestBr3ArchiveAware:
    def test_a_cancelled_archived_row_is_NOT_resurrected(
        self, guard_engine, results_root
    ):
        _insert_archive(
            guard_engine,
            status=SourceStatus.CANCELLED.value,
            config=_chain_config(datetime.now(timezone.utc) - timedelta(days=30)),
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "noop_archived_cancelled"
        assert _rows(guard_engine) == []

    def test_an_archived_stopped_row_WITH_a_chain_block_is_revived(
        self, guard_engine, results_root
    ):
        stale = datetime.now(timezone.utc) - timedelta(days=30)
        archived_config = _chain_config(stale)
        _insert_archive(
            guard_engine,
            status=SourceStatus.STOPPED.value,
            config=archived_config,
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "revived"
        rows = _rows(guard_engine)
        assert len(rows) == 1
        assert rows[0].enabled is True
        assert rows[0].status == SourceStatus.RUNNING.value
        # The snapshot's other keys are carried over verbatim.
        assert rows[0].config["agent"] == "maintenancer"
        assert rows[0].config["message"] == WEEKLY_RUN_MESSAGE
        assert rows[0].config["chain"]["mode"] == CHAIN_MODE

    def test_an_archived_stopped_row_WITHOUT_a_chain_block_is_not_revived(
        self, guard_engine, results_root
    ):
        _insert_archive(
            guard_engine,
            status=SourceStatus.STOPPED.value,
            config={"agent": "someone-else", "schedule": "0 8 * * *"},
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "created"
        rows = _rows(guard_engine)
        assert len(rows) == 1
        assert rows[0].config["agent"] == "maintenancer"

    def test_a_missing_row_with_no_archive_is_created(
        self, guard_engine, results_root
    ):
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "created"
        assert len(_rows(guard_engine)) == 1

    def test_a_present_cancelled_row_is_respected(self, guard_engine, results_root):
        past = datetime.now(timezone.utc) - timedelta(days=2)
        original = past.isoformat()
        _insert_row(
            guard_engine,
            config=_chain_config(past),
            enabled=False,
            status=SourceStatus.CANCELLED.value,
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "noop_cancelled"
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.status == SourceStatus.CANCELLED.value
        assert row.enabled is False
        assert row.config["run_at"] == original

    def test_a_present_paused_row_is_respected(self, guard_engine, results_root):
        """A paused schedule is ``enabled=False`` — the operator's intent
        is never overwritten by the guard."""
        past = datetime.now(timezone.utc) - timedelta(days=2)
        original = past.isoformat()
        _insert_row(
            guard_engine,
            config=_chain_config(past),
            enabled=False,
            status=SourceStatus.STOPPED.value,
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "noop_paused"
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.enabled is False
        assert row.status == SourceStatus.STOPPED.value
        assert row.config["run_at"] == original

    def test_a_present_stopped_chain_row_is_re_enabled(
        self, guard_engine, results_root
    ):
        """Plan Task 1.4 (iii-b): a STOPPED row that still carries a
        chain block is re-enabled at boot (a stopped row is skipped by
        the registry's boot filter, so the status must be promoted)."""
        _insert_row(
            guard_engine,
            config=_chain_config(datetime.now(timezone.utc) + timedelta(days=3)),
            enabled=True,
            status=SourceStatus.STOPPED.value,
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "reenabled"
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.status == SourceStatus.RUNNING.value
        assert row.enabled is True

    def test_a_present_stopped_row_WITHOUT_a_chain_block_is_left_alone(
        self, guard_engine, results_root
    ):
        _insert_row(
            guard_engine,
            config={"agent": "someone-else", "run_at": "2030-01-01T00:00:00+00:00"},
            enabled=True,
            status=SourceStatus.STOPPED.value,
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "noop_existing"
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.status == SourceStatus.STOPPED.value


# ══════════════════════════════════════════════════════════════════════════
# BR-15 — the reportless-week detector (surface-ONLY)
# ══════════════════════════════════════════════════════════════════════════


class TestBr15ReportlessWeek:
    def test_no_execution_record_means_nothing_to_detect(
        self, guard_engine, results_root
    ):
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        from daemon.repositories.source.repository import SQLModelSourceRepository

        source_repo = SQLModelSourceRepository(guard_engine)
        assert (
            detect_reportless_week(
                source_repo, source_id=SEED_SOURCE_ID, results_root=results_root
            )
            is None
        )

    def test_an_empty_root_is_not_a_reportless_week(self, guard_engine, results_root):
        """The D4 cure — the EMPTY-root case is NOT a reportless week: it
        is the moved/lost-root shape and belongs to
        :func:`detect_results_root_empty`.  Surfacing it as a routine
        reportless week IS the D4 false-positive (a promote moves the
        root ⇒ every post-promote boot cried reportless)."""
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        with Session(guard_engine) as session:
            session.add(
                ScheduleExecution(
                    execution_id=str(uuid.uuid4()),
                    schedule_id=SEED_SOURCE_ID,
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                    status="completed",
                )
            )
            session.commit()
        from daemon.repositories.source.repository import SQLModelSourceRepository

        source_repo = SQLModelSourceRepository(guard_engine)
        assert (
            detect_reportless_week(
                source_repo, source_id=SEED_SOURCE_ID, results_root=results_root
            )
            is None
        )

    def test_a_fire_with_a_report_is_not_a_miss(self, guard_engine, results_root):
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        artifact_root = maintenance_results_dir(results_root)
        artifact_root.mkdir(parents=True, exist_ok=True)
        # The fire happened an hour ago; the report landed after it.
        with Session(guard_engine) as session:
            session.add(
                ScheduleExecution(
                    execution_id=str(uuid.uuid4()),
                    schedule_id=SEED_SOURCE_ID,
                    triggered_at=(
                        datetime.now(timezone.utc) - timedelta(hours=1)
                    ).isoformat(),
                    status="completed",
                )
            )
            session.commit()
        (artifact_root / "run-20261122_030000_deadbeef.md").write_text(
            "# run\n", encoding="utf-8"
        )
        from daemon.repositories.source.repository import SQLModelSourceRepository

        source_repo = SQLModelSourceRepository(guard_engine)
        finding = detect_reportless_week(
            source_repo, source_id=SEED_SOURCE_ID, results_root=results_root
        )
        assert finding is None


# ══════════════════════════════════════════════════════════════════════════
# D4 — the fresh-root guard (a MOVED/LOST artifact root)
# ══════════════════════════════════════════════════════════════════════════


class TestD4FreshRootGuard:
    """The D4 cure — 'no runs ever' vs 'root moved/lost'."""

    def test_no_execution_record_means_no_finding(self, guard_engine, results_root):
        """'No runs ever' — with no durable last-fire state the empty root
        is the fresh-install state, NOT a moved/lost root."""
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        from daemon.repositories.source.repository import SQLModelSourceRepository

        source_repo = SQLModelSourceRepository(guard_engine)
        assert (
            detect_results_root_empty(
                source_repo, source_id=SEED_SOURCE_ID, results_root=results_root
            )
            is None
        )

    def test_a_fired_chain_with_an_empty_root_is_a_moved_root(
        self, guard_engine, results_root
    ):
        """Durable last-fire state EXISTS and the root holds no run-*.md ⇒
        the moved/lost-root finding — a DISTINCT kind, never a reportless
        week."""
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        with Session(guard_engine) as session:
            session.add(
                ScheduleExecution(
                    execution_id=str(uuid.uuid4()),
                    schedule_id=SEED_SOURCE_ID,
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                    status="completed",
                )
            )
            session.commit()
        from daemon.repositories.source.repository import SQLModelSourceRepository

        source_repo = SQLModelSourceRepository(guard_engine)
        finding = detect_results_root_empty(
            source_repo, source_id=SEED_SOURCE_ID, results_root=results_root
        )
        assert finding is not None
        assert finding["kind"] == FINDING_KIND_RESULTS_ROOT_EMPTY
        assert finding["source_id"] == SEED_SOURCE_ID
        assert finding["latest_report"] is None
        assert "MOVED" in finding["detail"]

    def test_a_root_holding_a_report_is_not_a_moved_root(
        self, guard_engine, results_root
    ):
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        artifact_root = maintenance_results_dir(results_root)
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "run-20261122_030000_deadbeef.md").write_text(
            "# run\n", encoding="utf-8"
        )
        with Session(guard_engine) as session:
            session.add(
                ScheduleExecution(
                    execution_id=str(uuid.uuid4()),
                    schedule_id=SEED_SOURCE_ID,
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                    status="completed",
                )
            )
            session.commit()
        from daemon.repositories.source.repository import SQLModelSourceRepository

        source_repo = SQLModelSourceRepository(guard_engine)
        assert (
            detect_results_root_empty(
                source_repo, source_id=SEED_SOURCE_ID, results_root=results_root
            )
            is None
        )

    def test_the_board_notice_fires_once_across_boots(
        self, guard_engine, results_root
    ):
        """The durable de-dup — the moved-root notice writes ONE board
        item; a second surfacing pass (the next boot) does NOT duplicate
        it.  The notice carries its OWN tag (never the reportless-week
        tag), so the two episodes can never mask each other."""
        from daemon.repositories.source.maintenance_seed import (
            _detect_and_surface_results_root_empty,
        )
        from daemon.repositories.source.repository import SQLModelSourceRepository
        from daemon.services.board_service import BoardService

        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        with Session(guard_engine) as session:
            session.add(
                ScheduleExecution(
                    execution_id=str(uuid.uuid4()),
                    schedule_id=SEED_SOURCE_ID,
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                    status="completed",
                )
            )
            session.commit()
        source_repo = SQLModelSourceRepository(guard_engine)
        board = BoardService(engine=guard_engine)

        first = _detect_and_surface_results_root_empty(
            source_repo,
            source_id=SEED_SOURCE_ID,
            results_root=results_root,
            engine=guard_engine,
        )
        assert first is not None
        opened = board.list_items(status="open", tag="results-root-empty", limit=5)
        assert int(opened.get("count") or 0) == 1

        # The next boot: the finding repeats, the board item does not.
        second = _detect_and_surface_results_root_empty(
            source_repo,
            source_id=SEED_SOURCE_ID,
            results_root=results_root,
            engine=guard_engine,
        )
        assert second is not None
        opened = board.list_items(status="open", tag="results-root-empty", limit=5)
        assert int(opened.get("count") or 0) == 1


# ══════════════════════════════════════════════════════════════════════════
# P5 T5.4 — the chain-break guard legs (a)–(e): the EXACT BR-1/BR-3
# semantics (phase5-plan.md Task 5.4).
#
# Relationship to the P1 guards above: those pin the guard's DECISION
# TABLE; these pin the OPERATIVE PREDICATES with the real substrate —
# the BR-3 legs (c)/(d) drive the REAL source-GC archive path
# (``cancel_source_config`` = the atomic operator-cancel write,
# ``gc_archive_and_delete`` = the real GC predicate + archive INSERT),
# so the archive row the guard consults is the one the GC actually
# wrote.
#
# ANCHORS VERIFIED IN CODE (2026-11-22 — the plan's anchors were leads):
# * the adapter's past-due auto-fire AND its lateness-cap comparison
#   both live in ``SchedulerAdapter._get_next_trigger_time``
#   (daemon/sources/adapters/scheduler.py:1147-1185 — ``if run_at <= now:``
#   then ``if lateness > cap:`` → PAST_LATENESS_CAP / ``return now``);
#   NOT at scheduler.py:924-956 (that region is ``_on_complete`` /
#   ``_rearm_chain_row``).
# * the atomic operator-cancel write:
#   ``SQLModelSourceRepository.cancel_source_config``
#   (daemon/repositories/source/repository.py:604-660 — ONE session
#   writes ``enabled=False`` + ``status='cancelled'``).
# * the GC predicate + the fail-loud archive INSERT:
#   ``gc_archive_and_delete`` / ``_archive_rows``
#   (daemon/repositories/source/repository.py:814-1000).
# ══════════════════════════════════════════════════════════════════════════

#: The T5.4 substrate = the guard tables PLUS the source-GC reachable
#: set — ``gc_archive_and_delete`` child-deletes ``instance_mappings`` /
#: ``processed_external_messages`` and archive-writes
#: ``schedule_executions_archive``.
T54_GUARD_TABLES = GUARD_TABLES + [
    InstanceMapping.__table__,
    ProcessedMessage.__table__,
    ScheduleExecutionArchive.__table__,
]


@pytest.fixture
def t54_engine(tmp_path) -> Engine:
    """The T5.4 engine — the guard tables plus the GC-reachable tables.

    A SEPARATE fixture (the P1 ``guard_engine`` above is left untouched)
    so the real-GC legs never widen the P1 legs' substrate."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 't54_chain_guard.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(engine, tables=T54_GUARD_TABLES)
    try:
        yield engine
    finally:
        engine.dispose()


def _t54_repo(engine: Engine):
    from daemon.repositories.source.repository import SQLModelSourceRepository

    return SQLModelSourceRepository(engine)


def _archive_row(engine: Engine) -> SourceConfigArchive | None:
    with Session(engine) as session:
        return session.get(SourceConfigArchive, SEED_SOURCE_ID)


def _gc_now(days_ahead: int = 40) -> datetime:
    """The GC's reference instant — 40 days past the row's fresh
    ``updated_at``, so the predicate's AGE leg passes at the shipped
    default retention (14 days).  The GC API takes ``now``; advancing it
    is the honest way to simulate the retention window elapsing (no
    fixture-row back-dating)."""
    return datetime.now(timezone.utc) + timedelta(days=days_ahead)


def _stop_and_disable(repo: Any, *, source_id: str = SEED_SOURCE_ID) -> None:
    """Write the operator-pause shape (``enabled=False`` +
    ``status='stopped'``) through the repository seam the scheduling
    service's pause path uses
    (``daemon/services/scheduling_service.py:1122-1158``; the tool-side
    knob is ``TaskScheduleUpdateInput.paused``,
    ``daemon/tools/scheduling.py:124-129``)."""
    repo.update_source_config(source_id, enabled=False)
    repo.update_source_status(source_id, SourceStatus.STOPPED.value)


class TestP5T54Br1Exact:
    """BR-1 — the past-``run_at`` re-mint predicate, EXACT.

    Operative predicate (phase5-plan.md T5.4): a re-mint happens ONLY
    when a cap is configured AND ``now - run_at > cap`` — the SAME
    strict comparison the adapter applies at its fire tick
    (``_get_next_trigger_time``: ``if lateness > cap:`` ⇒ SKIP).  At the
    exact boundary (``lateness == cap``) the adapter still FIRES, so the
    guard must leave ``run_at`` alone."""

    def test_leg_a_cap_unset_leaves_the_past_run_at_for_the_auto_fire(
        self, guard_engine, results_root, monkeypatch
    ):
        """Leg (a): ``SchedulingConfig.one_shot_max_lateness_seconds``
        UNSET (``None`` = unlimited, ``daemon/config.py:3362-3368``)
        against a row whose ``run_at`` is PAST ⇒ the guard LEAVES
        ``run_at`` UNTOUCHED — the adapter's past-due auto-fire delivers
        the late run.  Asserting a re-mint here is FORBIDDEN: it would
        pre-empt the auto-fire and silently skip the missed week."""
        monkeypatch.delenv(CAP_ENV, raising=False)
        from daemon.config import SchedulingConfig

        # The premise is the CONFIG VALUE, not the env var: the cap the
        # guard will read really is None (unlimited).
        assert SchedulingConfig().one_shot_max_lateness_seconds is None

        now = datetime.now(timezone.utc)
        config = _chain_config(now - timedelta(days=2))
        _insert_row(guard_engine, config=config)

        outcome = seed_central_maintenance_schedule(
            guard_engine, now=now, results_root=results_root
        )
        assert outcome.action == "noop_existing"
        # The operative BR-1 branch: the past run_at was LEFT for the
        # adapter's past-due auto-fire (NOT "within cap" — the cap was
        # unset; the two no-ops are distinct outcomes).
        assert outcome.detail == "past_run_at_left_for_auto_fire"
        assert outcome.run_at == config["run_at"]

        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        # A re-mint is FORBIDDEN — byte-for-byte untouched.
        assert row.config["run_at"] == config["run_at"]
        assert row.config["chain"]["run_at_utc"] == config["chain"]["run_at_utc"]
        assert row.config == config  # the guard wrote NOTHING
        # ... and the row stays ARMED so the auto-fire can deliver it.
        assert row.enabled is True
        assert row.status == SourceStatus.RUNNING.value

    def test_leg_b_cap_exceeded_re_mints_a_future_instant(
        self, guard_engine, results_root, monkeypatch
    ):
        """Leg (b): cap configured + ``now - run_at > cap`` ⇒ the guard
        RE-MINTS the next ``run_at`` — mirroring the adapter's own cap
        comparison (``_get_next_trigger_time`` skips when
        ``lateness > cap``; the guard re-mints on the same predicate so
        the chain never stalls into a skipped fire)."""
        monkeypatch.setenv(CAP_ENV, "3600")
        from daemon.config import SchedulingConfig

        assert SchedulingConfig().one_shot_max_lateness_seconds == 3600

        now = datetime.now(timezone.utc)
        config = _chain_config(now - timedelta(seconds=3601))  # lateness > cap
        _insert_row(guard_engine, config=config)

        outcome = seed_central_maintenance_schedule(
            guard_engine,
            now=now,
            results_root=results_root,
            rng=random.Random(20261122),
        )
        assert outcome.action == "reminted"
        assert outcome.detail == "reminted"

        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.config["run_at"] != config["run_at"]
        minted = datetime.fromisoformat(row.config["run_at"].replace("Z", "+00:00"))
        assert minted > now  # a FUTURE instant — the chain lives
        # One concept, one marker: the chain block's UTC echo moved WITH it.
        assert row.config["chain"]["run_at_utc"] == row.config["run_at"]
        assert outcome.run_at == row.config["run_at"]
        # Every other key carried over verbatim (full-replace safety).
        for key in ("agent", "message", "recurrence", "instance_mode", "project_id"):
            assert row.config[key] == config[key]
        assert row.enabled is True
        assert row.status == SourceStatus.RUNNING.value

    def test_leg_b_boundary_lateness_exactly_the_cap_is_not_a_breach(
        self, guard_engine, results_root, monkeypatch
    ):
        """The mirror pin: ``lateness == cap`` is NOT a breach.  The
        adapter's comparison is strict (``lateness > cap`` ⇒ skip), so
        AT the boundary the adapter still fires — the guard must leave
        ``run_at`` alone (``past_run_at_within_cap``, never ``reminted``)."""
        monkeypatch.setenv(CAP_ENV, "3600")
        now = datetime.now(timezone.utc)
        config = _chain_config(now - timedelta(seconds=3600))  # lateness == cap
        _insert_row(guard_engine, config=config)

        outcome = seed_central_maintenance_schedule(
            guard_engine, now=now, results_root=results_root
        )
        assert outcome.action == "noop_existing"
        assert outcome.detail == "past_run_at_within_cap"
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.config["run_at"] == config["run_at"]
        assert row.config == config


class TestP5T54Br3ArchiveExact:
    """BR-3 — the archive-aware recreate, EXACT, through the REAL
    source-GC substrate: the cancelled/stopped rows below are archived
    by the real ``gc_archive_and_delete`` (real predicate + real archive
    INSERT into ``source_configs_archive``), not hand-inserted."""

    def test_leg_c_cancelled_archived_is_never_resurrected(
        self, t54_engine, results_root
    ):
        """Leg (c): an operator-cancelled chain (``enabled=False`` +
        ``status='cancelled'``, written ATOMICALLY by
        ``cancel_source_config``) that source-GC has ARCHIVED leaves the
        live row ABSENT ⇒ the guard MUST NOT recreate it.

        The archived snapshot even CARRIES the chain block — the guard
        must still refuse: the archive STATUS decides (a cancel is
        operator intent); the chain block is only the revive marker for
        a STOPPED row."""
        repo = _t54_repo(t54_engine)
        stale = datetime.now(timezone.utc) - timedelta(days=30)
        _insert_row(t54_engine, config=_chain_config(stale))

        # The operator cancels — ONE atomic write (enabled + status).
        cancelled = repo.cancel_source_config(SEED_SOURCE_ID)
        assert cancelled is not None
        assert cancelled.enabled is False
        assert cancelled.status == SourceStatus.CANCELLED.value

        # Source-GC collects it: the real predicate + the real archive
        # INSERT (the retention window is simulated by advancing ``now``).
        result = repo.gc_archive_and_delete(
            source_id=SEED_SOURCE_ID, now=_gc_now(), retention_days=14
        )
        assert result.skipped_reason is None
        assert result.deleted is True
        assert result.archived_config is True

        # The live row is ABSENT; the archive row holds the cancelled
        # snapshot — WITH the chain block in it.
        assert _row(t54_engine, SEED_SOURCE_ID) is None
        archived = _archive_row(t54_engine)
        assert archived is not None
        assert archived.status == SourceStatus.CANCELLED.value
        assert archived.enabled is False
        assert json.loads(archived.config or "{}").get("chain", {}).get(
            "mode"
        ) == CHAIN_MODE

        # The guard boots: the cancelled chain is NEVER resurrected.
        outcome = seed_central_maintenance_schedule(
            t54_engine, results_root=results_root
        )
        assert outcome.action == "noop_archived_cancelled"
        assert _rows(t54_engine) == []  # NOT recreated — the operative assertion
        # ... and the guard pass never touches the audit trail.
        after = _archive_row(t54_engine)
        assert after is not None
        assert after.config == archived.config
        assert after.status == SourceStatus.CANCELLED.value

    def test_leg_d_stopped_archived_with_chain_block_is_revived(
        self, t54_engine, results_root
    ):
        """Leg (d): an archived row with ``status='stopped'`` whose
        archived config snapshot CARRIES a chain block ⇒ the guard
        revives it — the SAME source_id keying as the re-arm seam (one
        concept, one marker)."""
        repo = _t54_repo(t54_engine)
        future = datetime.now(timezone.utc) + timedelta(days=3)
        config = _chain_config(future)
        _insert_row(t54_engine, config=config)

        _stop_and_disable(repo)
        result = repo.gc_archive_and_delete(
            source_id=SEED_SOURCE_ID, now=_gc_now(), retention_days=14
        )
        assert result.skipped_reason is None
        assert result.deleted is True
        assert _row(t54_engine, SEED_SOURCE_ID) is None
        archived = _archive_row(t54_engine)
        assert archived is not None
        assert archived.status == SourceStatus.STOPPED.value

        outcome = seed_central_maintenance_schedule(
            t54_engine, results_root=results_root
        )
        assert outcome.action == "revived"
        rows = _rows(t54_engine)
        assert len(rows) == 1
        row = rows[0]
        # Same keying as the re-arm seam: the SAME source_id comes back.
        assert row.source_id == SEED_SOURCE_ID
        assert row.enabled is True
        assert row.status == SourceStatus.RUNNING.value
        # The snapshot is the SOLE reconstruction input — carried verbatim.
        assert row.config["agent"] == config["agent"]
        assert row.config["message"] == config["message"]
        assert row.config["recurrence"] == config["recurrence"]
        assert row.config["chain"]["mode"] == CHAIN_MODE
        assert row.config["chain"]["run_at_utc"] == config["chain"]["run_at_utc"]
        # The archive row is NOT consumed by the revive (audit trail stays).
        assert _archive_row(t54_engine) is not None

    def test_leg_d_contrast_stopped_archived_without_chain_is_not_revived(
        self, t54_engine, results_root
    ):
        """Leg (d) contrast — ONE concept, ONE marker: the same archived
        shape WITHOUT a chain block is NOT revived (a fresh chain is
        created instead).  The chain block is the only revive key; a
        foreign archived schedule must never be resurrected as the
        maintenance chain."""
        repo = _t54_repo(t54_engine)
        _insert_row(
            t54_engine,
            config={"agent": "someone-else", "schedule": "0 8 * * *"},
        )
        _stop_and_disable(repo)
        result = repo.gc_archive_and_delete(
            source_id=SEED_SOURCE_ID, now=_gc_now(), retention_days=14
        )
        assert result.skipped_reason is None
        assert result.deleted is True

        outcome = seed_central_maintenance_schedule(
            t54_engine, results_root=results_root
        )
        assert outcome.action == "created"  # NOT "revived"
        row = _row(t54_engine, CANONICAL_SEED_SOURCE_ID)
        assert row is not None
        # A FRESH CANONICAL row — never the foreign snapshot dressed up
        # as one, and never a chain row (architecture v3 §4.3).
        assert row.config["agent"] == "maintenancer"
        assert row.config["recurrence"] == "weekly"
        assert "chain" not in row.config

    def test_leg_e_missing_row_with_no_archive_is_created_deterministically(
        self, guard_engine, results_root
    ):
        """Leg (e): a missing row with NO archive record ⇒ created —
        exactly as the seed lifecycle mirror prescribes (the DETERMINISTIC
        uuid5 PK, mirroring
        ``tests/unit/repositories/test_tool_usage_seed_lifecycle.py:140-145``),
        which is what makes the next boot a no-op on the SAME row."""
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "created"
        # The deterministic uuid5 identity (mirror of the tool-usage
        # seed lifecycle pin) — now the CANONICAL identity, under its own
        # DISTINCT label (architecture v3 §4.3).
        assert CANONICAL_SEED_SOURCE_ID == str(
            uuid.uuid5(uuid.NAMESPACE_DNS, CANONICAL_SEED_NAME)
        )
        assert CANONICAL_SEED_NAME != SEED_NAME
        rows = _rows(guard_engine)
        assert len(rows) == 1
        row = rows[0]
        assert row.source_id == CANONICAL_SEED_SOURCE_ID
        assert row.name == CANONICAL_SEED_NAME
        assert row.source_type == SEED_SOURCE_TYPE
        assert row.enabled is True
        assert row.status == SourceStatus.RUNNING.value
        # CANONICAL shape: no chain block, a cron expression instead.
        assert "chain" not in row.config
        assert row.config["recurrence"] == "weekly"
        assert row.config["schedule"]
        assert outcome.source_id == CANONICAL_SEED_SOURCE_ID
        # The uuid5 keying makes the next boot a no-op on the SAME row.
        second = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert second.action == "noop_existing"
        assert len(_rows(guard_engine)) == 1


class TestP5T54AlsoStillProven:
    """T5.4 'also still proven' — operator intent over BR-1, and the
    archive re-archive path's PK discipline."""

    def test_a_present_cancelled_row_is_respected_even_with_the_cap_exceeded(
        self, t54_engine, results_root, monkeypatch
    ):
        """A PRESENT row the operator cancelled ATOMICALLY stays
        untouched even when the cap IS exceeded — the cancelled check
        PRECEDES the BR-1 re-mint, so BR-1 must never pre-empt operator
        intent (a naive reorder REDs this leg)."""
        monkeypatch.setenv(CAP_ENV, "3600")
        repo = _t54_repo(t54_engine)
        config = _chain_config(datetime.now(timezone.utc) - timedelta(days=2))
        _insert_row(t54_engine, config=config)
        cancelled = repo.cancel_source_config(SEED_SOURCE_ID)
        assert cancelled.status == SourceStatus.CANCELLED.value

        outcome = seed_central_maintenance_schedule(
            t54_engine, results_root=results_root
        )
        assert outcome.action == "noop_cancelled"  # NEVER "reminted"
        row = _row(t54_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.status == SourceStatus.CANCELLED.value
        assert row.enabled is False  # not re-enabled
        assert row.config["run_at"] == config["run_at"]  # no re-mint

    def test_a_present_paused_row_is_respected(self, t54_engine, results_root):
        """The pause shape the scheduling seam writes
        (``enabled=False`` + ``status='stopped'``) is respected: the
        guard does not re-enable it and does not re-mint its past
        ``run_at``."""
        repo = _t54_repo(t54_engine)
        config = _chain_config(datetime.now(timezone.utc) - timedelta(days=2))
        _insert_row(t54_engine, config=config)
        _stop_and_disable(repo)

        outcome = seed_central_maintenance_schedule(
            t54_engine, results_root=results_root
        )
        assert outcome.action == "noop_paused"
        row = _row(t54_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.enabled is False
        assert row.status == SourceStatus.STOPPED.value
        assert row.config["run_at"] == config["run_at"]

    def test_the_re_archive_path_never_collides_the_archive_pk(
        self, t54_engine, results_root
    ):
        """The revive keying (the SAME source_id) creates the re-archive
        scenario: when source-GC later collects the revived row, the
        archive INSERT hits the ``source_configs_archive`` PK — the path
        must FAIL LOUD (skip ``archive_id_collision``), never silently
        overwrite the stale snapshot and never destroy the live row."""
        repo = _t54_repo(t54_engine)
        _insert_row(
            t54_engine,
            config=_chain_config(datetime.now(timezone.utc) + timedelta(days=3)),
        )
        _stop_and_disable(repo)
        first = repo.gc_archive_and_delete(
            source_id=SEED_SOURCE_ID, now=_gc_now(), retention_days=14
        )
        assert first.deleted is True
        assert first.skipped_reason is None
        original = _archive_row(t54_engine)
        assert original is not None

        # The guard revives the archived stopped chain (leg (d) keying —
        # the SAME source_id); the operator then stops it again with a
        # MUTATED config so a silent overwrite would be VISIBLE.
        outcome = seed_central_maintenance_schedule(
            t54_engine, results_root=results_root
        )
        assert outcome.action == "revived"
        mutated = dict(
            _chain_config(datetime.now(timezone.utc) + timedelta(days=3))
        )
        mutated["agent"] = "someone-else"
        repo.update_source_config(SEED_SOURCE_ID, config=mutated, enabled=False)
        repo.update_source_status(SEED_SOURCE_ID, SourceStatus.STOPPED.value)

        second = repo.gc_archive_and_delete(
            source_id=SEED_SOURCE_ID, now=_gc_now(), retention_days=14
        )
        assert second.skipped_reason == "archive_id_collision"
        assert second.deleted is False
        assert second.archived_config is False
        # The live row SURVIVES (the rollback never reaches the deletes).
        assert repo.get_source_config(SEED_SOURCE_ID) is not None
        # The stale snapshot is NOT silently overwritten (fail-loud).
        after = _archive_row(t54_engine)
        assert after is not None
        assert after.config == original.config
        assert "someone-else" not in (after.config or "")
