"""Focused regression tests — the independent-review fix pass (F1–F7, F10).

Each test asserts the BEHAVIOUR the fix restored — a real call wherever a
real call is possible. TWO INTENTIONAL source-string pins remain (each
asserts a LITERAL contract with no behavioural seam):
``test_migration_sql_and_pg_mirror_name_the_same_columns`` (the SQLite
DDL and the PG mirror must name the same columns) and
``test_the_inline_datetime_call_is_gone_from_the_three_sites`` (the
retired inline timestamp call must not come back). Every other test is a
real call:

* **F1** — the boot DRY-RUN backfill is REACHABLE. The block runs AFTER
  ``self._mission_record_service`` is assigned, so the documented
  dry-run plan actually executes. At base the block sat ~200 lines
  earlier, where the attribute did not exist yet: the AttributeError was
  swallowed by the boot envelope and the plan NEVER ran / never logged on
  the default ``dry`` path.
* **F2/F3** — the scheduler lane-9 mint SITE is WIRED and INERT. The
  default configuration skips the site entirely (no service call, no
  ``mission_id`` kwarg → byte-identical pre-PR1 behavior); both gates
  open → the fail-open resolve-or-mint fires with the scheduler lane
  token (``founding_source='scheduler'``, commissioner NULL).
* **F4** — the ``report_injections`` ORM agrees with the DDL: the SQLite
  migration and the ``_ensure_postgres_columns`` PG mirror both add the
  two nullable TEXT columns, so ``create_all`` must produce them too.
* **F5** — the mode ladder resolves ONCE and is cached (restart-read);
  an unrecognized token fails the BOOT loudly (house switch pattern)
  while the lane wrappers stay fail-open.
* **F6** — the three TEXT-timestamp sites delegate to the sanctioned
  ``now_utc_iso()`` helper (byte-identical output).

ZERO-BEHAVIOR-CHANGE pins: nothing here writes outside the two
mission-record tables (+ the SQLite test database).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
from daemon.repositories.mission_record.models import MissionRecord
from daemon.repositories.report_injection.models import ReportInjection
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    SCHEDULER_MINT_EXTENSION_ENV,
    MissionRecordMode,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The sanctioned TEXT-timestamp shape (aware ISO-8601, UTC offset).
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+00:00$")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    """The mode ladder is CACHED (F5) — every test starts from a clean
    cache so the env it sets is the env that actually gets resolved."""
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def sqlite_engine(tmp_path: Path):
    """File-backed SQLite engine (NullPool, ``create_all``)."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'mission-review.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_system_default_project(engine) -> None:
    """The daemon provisions this row at boot — mirror it (the manager
    reads it while wiring the repositories)."""
    from daemon import constants
    from daemon.repositories.project.models import Project, ProjectStatus

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Project(
                project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
                name="_system_default",
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="mission-record review-fix tests",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()


def _manager_config():
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )

    return Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path="/tmp/mission-review-unused.db",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )


def _build_manager(engine):
    """A REAL ``InstanceManager`` over the file-backed SQLite engine.

    Mirrors the production wiring (``daemon/api.py`` lifespan): the
    migration runner is stubbed out (the test DB is created by
    ``create_all``) and the engine factory is redirected to the test
    engine. No daemon boot, no port binding, no PG.
    """
    from daemon.manager import InstanceManager

    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config",
            return_value=engine,
        ),
    ):
        return InstanceManager(_manager_config())


# ---------------------------------------------------------------------------
# F1 — the boot DRY-RUN backfill must be reachable
# ---------------------------------------------------------------------------


class TestF1BootBackfillOrdering:
    def test_boot_backfill_dry_run_is_reachable_after_assignment(
        self, sqlite_engine, monkeypatch
    ):
        """F1 — the boot block runs AFTER the service assignment.

        RED at base: the block sat ~200 lines earlier, so
        ``self._mission_record_service`` did not exist yet → the
        AttributeError was swallowed by the boot envelope and the
        dry-run plan NEVER ran.
        """
        _seed_system_default_project(sqlite_engine)
        monkeypatch.delenv(MISSION_RECORDS_MODE_ENV, raising=False)

        calls: list[bool] = []

        def _spy(self, *, dry_run: bool = True, session=None):
            calls.append(dry_run)
            return {"scanned": 0}

        monkeypatch.setattr(
            MissionRecordService,
            "backfill_advisory_nudge_commissions",
            _spy,
        )

        _build_manager(sqlite_engine)

        assert calls == [True], (
            "the boot dry-run backfill never reached the service — the "
            "block still runs before self._mission_record_service exists"
        )

    def test_boot_fails_loud_on_unrecognized_mode_token(
        self, sqlite_engine, monkeypatch
    ):
        """F5 — the house switch pattern at the BOOT resolution site.

        An unrecognized ``ENSEMBLE_MISSION_RECORDS`` token must fail the
        daemon start loudly (ValueError naming the env var) instead of
        silently degrading a live lane.
        """
        _seed_system_default_project(sqlite_engine)
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "shipped-yesterday")

        with pytest.raises(ValueError) as excinfo:
            _build_manager(sqlite_engine)
        assert MISSION_RECORDS_MODE_ENV in str(excinfo.value)

    def test_boot_succeeds_in_off_mode_without_calling_the_backfill(
        self, sqlite_engine, monkeypatch
    ):
        """F5 — the ``off`` mode still skips the backfill entirely."""
        _seed_system_default_project(sqlite_engine)
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, MissionRecordMode.OFF)

        calls: list[bool] = []

        def _spy(self, *, dry_run: bool = True, session=None):
            calls.append(dry_run)
            return {"scanned": 0}

        monkeypatch.setattr(
            MissionRecordService,
            "backfill_advisory_nudge_commissions",
            _spy,
        )

        _build_manager(sqlite_engine)

        assert calls == []


# ---------------------------------------------------------------------------
# F2/F3 — the scheduler lane-9 mint site: wired, and inert by default
# ---------------------------------------------------------------------------


class _FakeMapper:
    """Stands in for ``daemon.sources.mapper.InstanceMapper``."""

    instance_id = "inst-scheduler-0001"

    def __init__(self, source_repo, manager):
        self._source_repo = source_repo
        self._manager = manager

    async def get_or_create_instance(self, **kwargs):
        return self.instance_id


class _FakeManager:
    """The manager surface ``_route_via_job_queue`` actually touches."""

    def __init__(self, mission_record_service=None):
        self.mission_record_service = mission_record_service
        self.enqueue_calls: list[dict] = []

    async def enqueue_message_job(self, **kwargs):
        self.enqueue_calls.append(kwargs)
        return SimpleNamespace(job_id="job-1", message_id="msg-1")


def _build_scheduler_adapter(manager):
    """A ``SchedulerAdapter`` with ONLY the attributes
    ``_route_via_job_queue`` touches (no ``__init__`` side effects)."""
    from daemon.models import SchedulerInstanceMode
    from daemon.sources.adapters.scheduler import SchedulerAdapter

    adapter = SchedulerAdapter.__new__(SchedulerAdapter)
    adapter.config = SimpleNamespace(source_id="sched-1")
    adapter._agent = "tester"
    adapter._manager = manager
    adapter._source_repo = None
    adapter._instance_mode = SchedulerInstanceMode.REUSE_INSTANCE
    adapter._schedule_type = SchedulerAdapter.SCHEDULE_TYPE_CRON
    adapter._run_at = None
    adapter._priority = 5
    adapter._execution_callback = None
    return adapter


def _install_scheduler_patches(monkeypatch):
    """Patch the two externals ``_route_via_job_queue`` reaches for."""
    import daemon.sources.adapters.scheduler as scheduler_module

    monkeypatch.setattr(
        scheduler_module,
        "get_registry",
        lambda: SimpleNamespace(resolve_to_id=lambda agent_id: agent_id),
    )
    monkeypatch.setattr("daemon.sources.mapper.InstanceMapper", _FakeMapper)


async def _drive_scheduler(adapter) -> None:
    await adapter._route_via_job_queue(
        "exec-1", "scheduled body", {"agent": "tester"}
    )


class TestF2F3SchedulerMintSite:
    async def test_default_configuration_skips_the_mint_site_entirely(
        self, monkeypatch
    ):
        """F2/F3 — INERT BY DEFAULT: the site is skipped completely."""
        monkeypatch.delenv(MISSION_RECORDS_MODE_ENV, raising=False)
        monkeypatch.delenv(SCHEDULER_MINT_EXTENSION_ENV, raising=False)

        manager = _FakeManager(mission_record_service=object())
        adapter = _build_scheduler_adapter(manager)
        _install_scheduler_patches(monkeypatch)

        await _drive_scheduler(adapter)

        assert len(manager.enqueue_calls) == 1
        assert "mission_id" not in manager.enqueue_calls[0], (
            "the default configuration must not add the mission_id kwarg "
            "(byte-identical pre-PR1 behavior)"
        )

    async def test_both_gates_open_fires_the_scheduler_lane_mint(
        self, monkeypatch
    ):
        """F2/F3 — the wired site fires with the scheduler lane token."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, MissionRecordMode.DRY)
        monkeypatch.setenv(SCHEDULER_MINT_EXTENSION_ENV, "1")

        import daemon.sources.adapters.scheduler as scheduler_module

        spy_calls: list[tuple] = []

        def _spy(service, actor_instance_id, lane, **kwargs):
            spy_calls.append((service, actor_instance_id, lane, kwargs))
            return "mission-scheduler-0001"

        monkeypatch.setattr(
            scheduler_module, "resolve_or_mint_fail_open", _spy
        )

        manager = _FakeManager(mission_record_service="svc")
        adapter = _build_scheduler_adapter(manager)
        _install_scheduler_patches(monkeypatch)

        await _drive_scheduler(adapter)

        assert len(spy_calls) == 1
        service, actor, lane, kwargs = spy_calls[0]
        assert service == "svc"
        assert actor == _FakeMapper.instance_id
        assert lane == "scheduler"
        assert kwargs.get("founding_source") == "scheduler"
        # the commissioner stays NULL (the daemon identity is the founder)
        assert "commissioner_instance_id" not in kwargs
        # and the minted id rides the enqueue
        assert len(manager.enqueue_calls) == 1
        assert manager.enqueue_calls[0]["mission_id"] == "mission-scheduler-0001"

    async def test_mode_off_keeps_the_site_inert_with_the_extension_flag(
        self, monkeypatch
    ):
        """F2/F3 — BOTH gates must open: mode ``off`` disarms the site."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, MissionRecordMode.OFF)
        monkeypatch.setenv(SCHEDULER_MINT_EXTENSION_ENV, "1")

        import daemon.sources.adapters.scheduler as scheduler_module

        spy_calls: list[tuple] = []

        def _spy(service, actor_instance_id, lane, **kwargs):
            spy_calls.append((service, actor_instance_id, lane, kwargs))
            return "mission-x"

        monkeypatch.setattr(
            scheduler_module,
            "resolve_or_mint_fail_open",
            _spy,
        )

        manager = _FakeManager(mission_record_service="svc")
        adapter = _build_scheduler_adapter(manager)
        _install_scheduler_patches(monkeypatch)

        await _drive_scheduler(adapter)

        assert spy_calls == []
        assert "mission_id" not in manager.enqueue_calls[0]

    async def test_extension_flag_off_keeps_the_site_inert_in_dry_mode(
        self, monkeypatch
    ):
        """F2/F3 — the mode ladder alone does NOT arm the site."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, MissionRecordMode.DRY)
        monkeypatch.delenv(SCHEDULER_MINT_EXTENSION_ENV, raising=False)

        import daemon.sources.adapters.scheduler as scheduler_module

        spy_calls: list[tuple] = []

        def _spy(service, actor_instance_id, lane, **kwargs):
            spy_calls.append((service, actor_instance_id, lane, kwargs))
            return "mission-x"

        monkeypatch.setattr(
            scheduler_module,
            "resolve_or_mint_fail_open",
            _spy,
        )

        manager = _FakeManager(mission_record_service="svc")
        adapter = _build_scheduler_adapter(manager)
        _install_scheduler_patches(monkeypatch)

        await _drive_scheduler(adapter)

        assert spy_calls == []
        assert "mission_id" not in manager.enqueue_calls[0]


# ---------------------------------------------------------------------------
# F4 — the report_injections ORM must agree with the DDL
# ---------------------------------------------------------------------------


class TestF4ReportInjectionOrmMatchesDdl:
    def test_orm_exposes_the_two_nullable_mission_columns(self):
        from sqlalchemy import Text

        columns = ReportInjection.__table__.columns
        for name in ("mission_id", "commissioner_instance_id"):
            assert name in columns, f"{name} missing from the ORM"
            col = columns[name]
            assert col.nullable, f"{name} must be nullable (additive DDL)"
            assert isinstance(col.type, Text), f"{name} must be TEXT (DDL parity)"

    def test_create_all_produces_the_two_columns(self, sqlite_engine):
        inspector = inspect(sqlite_engine)
        columns = {
            c["name"]: c
            for c in inspector.get_columns("report_injections")
        }
        for name in ("mission_id", "commissioner_instance_id"):
            assert name in columns, f"create_all did not produce {name}"
            assert columns[name]["nullable"] is True

    def test_migration_sql_and_pg_mirror_name_the_same_columns(self):
        migration = (
            REPO_ROOT
            / "daemon"
            / "migrations"
            / "versions"
            / "20261025_122730_add_mission_provenance_columns.sql"
        )
        sql = migration.read_text()
        assert "ALTER TABLE report_injections ADD COLUMN mission_id TEXT" in sql
        assert (
            "ALTER TABLE report_injections ADD COLUMN commissioner_instance_id TEXT"
            in sql
        )

        manager_src = (REPO_ROOT / "daemon" / "manager.py").read_text()
        assert (
            "ALTER TABLE report_injections ADD COLUMN IF NOT EXISTS mission_id TEXT"
            in manager_src
        )
        assert (
            "ALTER TABLE report_injections ADD COLUMN IF NOT EXISTS "
            "commissioner_instance_id TEXT" in manager_src
        )


# ---------------------------------------------------------------------------
# F6 — the sanctioned timestamp helper
# ---------------------------------------------------------------------------


class TestF6TimestampHelper:
    def test_utc_now_iso_helpers_match_the_sanctioned_format(self):
        from daemon.repositories.mission_record.models import (
            _utc_now_iso as models_stamp,
        )
        from daemon.repositories.mission_record.repository import (
            _utc_now_iso as repo_stamp,
        )

        assert _ISO_UTC_RE.match(models_stamp())
        assert _ISO_UTC_RE.match(repo_stamp())

    def test_the_inline_datetime_call_is_gone_from_the_three_sites(self):
        for rel in (
            "daemon/services/mission_record_service.py",
            "daemon/repositories/mission_record/repository.py",
            "daemon/repositories/mission_record/models.py",
        ):
            src = (REPO_ROOT / rel).read_text()
            assert "datetime.now(timezone.utc).isoformat()" not in src, (
                f"{rel} still inlines the timestamp call"
            )

    def test_settle_writes_the_sanctioned_timestamp_shape(self, sqlite_engine):
        """F6 — the settle stamp keeps the byte-identical TEXT shape."""
        from daemon.repositories.instance.models import Instance

        now_iso = datetime.now(timezone.utc).isoformat()
        with Session(sqlite_engine) as s:
            s.add(
                Instance(
                    instance_id="inst-settle-0001",
                    agent_id="worker",
                    agent_dir="agents/worker/inst-sett",
                    status="running",
                    project_id=None,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )
            s.add(
                MissionRecord(
                    mission_id="mission-settle-0001",
                    anchor_instance_id="inst-settle-0001",
                    epoch_seq=1,
                    founding_source="user_api",
                    status="open",
                    created_at=now_iso,
                )
            )
            s.commit()

        service = MissionRecordService(sqlite_engine)
        assert service.settle("mission-settle-0001") is True

        with Session(sqlite_engine) as s:
            row = s.get(MissionRecord, "mission-settle-0001")
            assert row.status == "settled"
            assert _ISO_UTC_RE.match(row.settled_at)
