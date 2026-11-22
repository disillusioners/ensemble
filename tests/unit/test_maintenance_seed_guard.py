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
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
        assert rows[0].source_id == SEED_SOURCE_ID
        assert rows[0].name == SEED_NAME
        assert rows[0].source_type == SEED_SOURCE_TYPE

    def test_the_seeded_row_carries_the_chain_block(self, guard_engine, results_root):
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        config = row.config
        assert config["agent"] == "maintenancer"
        assert config["message"] == WEEKLY_RUN_MESSAGE
        assert config["timezone"] == "Asia/Ho_Chi_Minh"
        assert config["recurrence"] == "once"
        assert config["instance_mode"] == "new_instance"
        assert config["project_id"]
        chain = config["chain"]
        assert chain["mode"] == CHAIN_MODE
        assert chain["window_start"] == "01:00"
        assert chain["window_end"] == "05:00"
        assert chain["weekday"] == 0
        assert chain["timezone"] == "Asia/Ho_Chi_Minh"
        assert chain["run_at_utc"] == config["run_at"]
        assert config["local_time"]

    def test_the_seeded_row_is_running_and_enabled(self, guard_engine, results_root):
        seed_central_maintenance_schedule(guard_engine, results_root=results_root)
        row = _row(guard_engine, SEED_SOURCE_ID)
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
