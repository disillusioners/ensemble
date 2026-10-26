"""Integration (REAL PostgreSQL) — the PR2 enforce reads + the F1 carry.

Why PostgreSQL and not SQLite
-----------------------------
Three of PR2's behaviours are dialect-sensitive:

* the F1 OPEN-EPOCH PARTIAL UNIQUE index — SQLite's partial-index
  support and PostgreSQL's are the same feature, but the INDEX
  CREATION over an existing table with duplicate open epochs is where
  the dialects differ in their failure modes (and the NORMALIZATION
  UPDATE that precedes it must be correct on both);
* the ``_ensure_postgres_columns`` MIRROR — the PostgreSQL mirror is
  the ONLY path that lands the index on an EXISTING PostgreSQL
  database (the ``.sql`` migration runner is a NO-OP on PG — see
  ``daemon/migrations/runner.py``). SQLite never exercises that
  mirror, so a SQLite-only suite cannot prove the PG half of the
  3-site pin;
* the boot ORDER — ``create_all`` → the migration runner →
  ``_ensure_postgres_columns`` — is the real production order, and the
  PG lane is the only one where all three actually do something.

The test drives the REAL boot path over a DEDICATED scratch database
(created from a maintenance connection, dropped + verified absent at
teardown) and NEVER touches ``ensemble_dev`` / ``ensemble_prod``.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session

# Model imports register the tables on ``SQLModel.metadata`` BEFORE
# ``create_all`` (tests/postgres/conftest.py convention).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
# The FULL model registration (the boot path registers every model
# before ``create_all`` — the ``_ensure_postgres_columns`` mirror
# creates indexes on tables this test's DB must therefore own).
import daemon.manager  # noqa: F401
import daemon.plugin_subsystem.drift_event_publisher  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import MissionRecord
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.attestation_ledger import safe_increment
from daemon.services.mission_record_reads import (
    count_outstanding_commissions,
    read_founding_message_id,
    read_mission_census,
)
from daemon.services.mission_record_service import (
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)

# ── PG scratch coordinates (PG_TEST_* ONLY — never ambient POSTGRES_*) ──
_PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
_PG_PORT = os.environ.get("PG_TEST_PORT", "5432")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "")
_PG_MAINT_DB = os.environ.get("PG_TEST_MAINT_DB", "postgres")
_PG_SCRATCH_PREFIX = "ensemble_mrpr2_reads_"

#: Live databases this fixture must NEVER touch (hard abort, not skip).
_PG_FORBIDDEN_DBS = frozenset({"ensemble_prod", "ensemble_dev"})


def _pg_admin_engine():
    """Maintenance engine (AUTOCOMMIT) for CREATE/DROP DATABASE."""
    if _PG_MAINT_DB in _PG_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: the PR2 PG fixture resolved maintenance DB "
            f"{_PG_MAINT_DB!r} — refusing to point the test at a live "
            f"database.",
            returncode=1,
        )
    url = (
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}"
    )
    engine = create_engine(
        url, isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — unreachable ⇒ clean skip
        engine.dispose()
        return None, f"PostgreSQL unreachable at {url}: {exc}"
    return engine, None


@pytest.fixture
def pg_scratch_engine() -> Iterator[tuple[Engine, str]]:
    """A DEDICATED scratch PostgreSQL database, created and dropped."""
    admin, skip_reason = _pg_admin_engine()
    if admin is None:
        pytest.skip(skip_reason)

    scratch_db = f"{_PG_SCRATCH_PREFIX}{uuid.uuid4().hex[:8]}"
    assert scratch_db not in _PG_FORBIDDEN_DBS
    engine: Engine | None = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
            # Autovacuum OFF for the scratch database: the background
            # worker attaches to a database it is vacuuming AS THE
            # CLUSTER OWNER (a superuser here), and ``DROP DATABASE ...
            # WITH (FORCE)`` cannot terminate a superuser's backend
            # without superuser rights — the drop would fail with
            # "must be a member of the role whose process is being
            # terminated" and leave the scratch database behind.
            # (PostgreSQL rejects a per-database ``autovacuum`` runtime
            # parameter change — the worker is handled by the drop
            # retry loop below instead.)
        scratch_url = (
            f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
            f"@{_PG_HOST}:{_PG_PORT}/{scratch_db}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True, future=True)
        yield engine, scratch_db
    finally:
        if engine is not None:
            engine.dispose()
        drop_error: Exception | None = None
        for _attempt in range(6):
            try:
                # The DROP FIRST (``WITH (FORCE)`` terminates the
                # attached backends itself). The explicit terminate
                # sweep is only the FALLBACK: terminating a SUPERUSER's
                # backend needs superuser rights, so a sweep that
                # raises would otherwise abort the drop and leave the
                # scratch database behind.
                with admin.connect() as conn:
                    conn.execute(
                        text(f'DROP DATABASE IF EXISTS "{scratch_db}" WITH (FORCE)')
                    )
                drop_error = None
                break
            except Exception as exc:  # noqa: BLE001 — retried
                drop_error = exc
                # A background worker (e.g. an autovacuum pass that was
                # already running when the scratch database was created)
                # releases its backend in seconds — the retry absorbs
                # it.
                time.sleep(1.0)
                try:
                    with admin.connect() as conn:
                        conn.execute(
                            text(
                                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                "WHERE datname = :db AND pid <> pg_backend_pid()"
                            ),
                            {"db": scratch_db},
                        )
                except Exception:  # noqa: BLE001 — best-effort sweep
                    pass
        # VERIFY ABSENT — the drop is not trusted on its own: a
        # leftover scratch database is a hygiene failure (the PR1
        # verification's "DB dropped, verified absent" receipt).
        if drop_error is None:
            with admin.connect() as conn:
                leftovers = list(
                    conn.execute(
                        text(
                            "SELECT datname FROM pg_database WHERE datname = :db"
                        ),
                        {"db": scratch_db},
                    )
                )
            if leftovers:
                drop_error = RuntimeError(
                    f"the scratch database {scratch_db!r} is still present "
                    f"after the drop"
                )
        admin.dispose()
        if drop_error is not None:
            raise RuntimeError(
                f"failed to drop the scratch PostgreSQL database "
                f"{scratch_db!r}: {drop_error}"
            )


@pytest.fixture
def booted_pg(pg_scratch_engine) -> tuple[Engine, str]:
    """The REAL boot path: create_all → the migration runner → the
    PostgreSQL ``_ensure_postgres_columns`` mirror."""
    from daemon.manager import InstanceManager
    from daemon.migrations.runner import MigrationRunner

    engine, scratch_db = pg_scratch_engine

    # 1. create_all (the manager's boot step).
    SQLModel.metadata.create_all(engine)

    # 2. The migration runner — a NO-OP on PostgreSQL (documented).
    runner = MigrationRunner(engine)
    assert runner.run_pending_migrations() == []

    # 3. The PostgreSQL schema-evolution mirror.
    class _Stub:
        pass

    stub = _Stub()
    stub._engine = engine
    # The mirror's tail call (the image_refs one-time sweep) is not
    # part of the mission-record schema — stub it out so the test
    # exercises the SCHEMA mirror only. The config handle gates the
    # boolean-repair tail block (``None`` ⇒ skipped).
    stub._migrate_overloaded_image_refs_rows = lambda: None
    stub._ensemble_config = None
    InstanceManager._ensure_postgres_columns(stub)

    return engine, scratch_db


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


def _seed_instance(engine, iid: str) -> str:
    with Session(engine) as session:
        if session.get(Instance, iid) is None:
            session.add(
                Instance(
                    instance_id=iid,
                    agent_id="leader",
                    agent_dir=f"agents/leader/{iid[:8]}",
                    status="running",
                    project_id=None,
                )
            )
            session.commit()
    return iid


class TestF1OnPostgres:
    def test_the_ensure_mirror_lands_the_index(self, booted_pg):
        """The PostgreSQL half of the 3-site pin: the index exists with
        the byte-identical name, and it is PARTIAL on ``status='open'``."""
        engine, _scratch = booted_pg
        inspector = inspect(engine)
        indexes = inspector.get_indexes("mission_records")
        by_name = {index["name"]: index for index in indexes}
        assert "ux_mission_records_anchor_open" in by_name
        index = by_name["ux_mission_records_anchor_open"]
        assert index.get("unique") is True
        assert index.get("column_names") == ["anchor_instance_id"]
        # The partial predicate (PostgreSQL renders it verbatim).
        predicate = index.get("dialect_options", {}).get("postgresql_where")
        assert predicate is not None
        assert "status" in str(predicate)
        assert "'open'" in str(predicate)

    def test_the_ensure_mirror_is_idempotent(self, booted_pg):
        """Re-running the mirror must not raise (the boot runs it on
        every start)."""
        from daemon.manager import InstanceManager

        engine, _scratch = booted_pg

        class _Stub:
            pass

        stub = _Stub()
        stub._engine = engine
        stub._migrate_overloaded_image_refs_rows = lambda: None
        stub._ensemble_config = None
        InstanceManager._ensure_postgres_columns(stub)
        InstanceManager._ensure_postgres_columns(stub)
        indexes = inspect(engine).get_indexes("mission_records")
        names = [index["name"] for index in indexes]
        assert names.count("ux_mission_records_anchor_open") == 1

    def test_a_second_open_epoch_is_rejected(self, booted_pg):
        """The F1 invariant as a CONSTRAINT on PostgreSQL."""
        engine, _scratch = booted_pg
        iid = _seed_instance(engine, str(uuid.uuid4()))
        service = MissionRecordService(engine)
        service.resolve_or_mint(iid, "user_api")

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            with Session(engine) as session:
                session.add(
                    MissionRecord(
                        anchor_instance_id=iid,
                        epoch_seq=99,
                        founding_source="backfill",
                        status="open",
                    )
                )
                session.commit()

    def test_closed_epochs_do_not_violate_the_index(self, booted_pg):
        engine, _scratch = booted_pg
        iid = _seed_instance(engine, str(uuid.uuid4()))
        service = MissionRecordService(engine)
        service.resolve_or_mint(iid, "user_api")
        with Session(engine) as session:
            session.add(
                MissionRecord(
                    anchor_instance_id=iid,
                    epoch_seq=98,
                    founding_source="backfill",
                    status="settled",
                )
            )
            session.commit()
        with Session(engine) as session:
            rows = list(
                session.exec(
                    text(
                        "SELECT status FROM mission_records "
                        "WHERE anchor_instance_id = :a"
                    ),
                    params={"a": iid},
                )
            )
        assert len(rows) == 2


class TestTheReadsOnPostgres:
    def test_the_census_anchor_and_count_over_pg(self, booted_pg):
        engine, _scratch = booted_pg
        anchor = _seed_instance(engine, str(uuid.uuid4()))
        worker = _seed_instance(engine, str(uuid.uuid4()))
        service = MissionRecordService(engine)
        resolution = service.resolve_or_mint(
            anchor, "user_api", founding_message_id="m-1"
        )
        service.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=anchor,
            worker_instance_id=worker,
            lane="instance_nudge",
        )

        assert read_mission_census(engine, anchor) is True
        assert read_founding_message_id(engine, anchor) == "m-1"
        assert count_outstanding_commissions(engine, anchor) == 1

    def test_the_amendment_over_pg(self, booted_pg):
        engine, _scratch = booted_pg
        anchor = _seed_instance(engine, str(uuid.uuid4()))
        service = MissionRecordService(engine)
        resolution = service.resolve_or_mint(
            anchor, "user_api", founding_message_id="m-1"
        )
        assert service.amend_anchor(resolution.mission_id, "m-2") is True
        assert read_founding_message_id(engine, anchor) == "m-2"

    def test_the_closed_epoch_is_not_amended_over_pg(self, booted_pg):
        engine, _scratch = booted_pg
        anchor = _seed_instance(engine, str(uuid.uuid4()))
        service = MissionRecordService(engine)
        resolution = service.resolve_or_mint(
            anchor, "user_api", founding_message_id="m-1"
        )
        service._repo.transition_epoch_status(
            resolution.mission_id, "settled"
        )
        assert service.amend_anchor(resolution.mission_id, "m-2") is False


class TestTheLedgerForwardingOnPostgres:
    """The production-dialect proof of the ledger alias forwarding.

    The unit guard (``tests/unit/test_mission_record_pr2_ledger_forwarding.py``)
    pins the SAME seam on SQLite. This leg re-drives it over the real
    PostgreSQL driver — the dialect production actually uses — because
    the defect class (a protocol alias that does not forward the epoch
    tag) silently DEGRADES THE DENY in production: the TypeError is
    swallowed by the C3 fail-open wrapper, the graph returns
    ``{"attestation_route": None}`` and the deny ledger is never
    written.
    """

    def test_safe_increment_writes_the_epoch_tagged_entry(self, booted_pg):
        engine, _scratch = booted_pg
        iid = _seed_instance(engine, str(uuid.uuid4()))
        repo = SQLModelInstanceRepository(engine)

        result = safe_increment(
            repo, iid, "fingerprint-1", mission_id="mission-a"
        )

        assert result == 1
        assert repo.get_attestation_denied_count(iid) == 1
        with Session(engine) as session:
            row = session.get(Instance, iid)
            entries = list(
                (row.instance_metadata or {}).get(
                    "attestation:denial_epochs"
                )
                or []
            )
        assert entries == [
            {"epoch": "fingerprint-1", "mission_id": "mission-a"}
        ]

    def test_the_untagged_call_shape_still_works(self, booted_pg):
        engine, _scratch = booted_pg
        iid = _seed_instance(engine, str(uuid.uuid4()))
        repo = SQLModelInstanceRepository(engine)

        assert safe_increment(repo, iid, "fingerprint-1") == 1
        with Session(engine) as session:
            row = session.get(Instance, iid)
            entries = list(
                (row.instance_metadata or {}).get(
                    "attestation:denial_epochs"
                )
                or []
            )
        assert entries == ["fingerprint-1"]


class TestScratchHygiene:
    def test_the_scratch_database_is_dropped(self, pg_scratch_engine):
        """The fixture drops the scratch DB — this test proves the
        teardown contract (and the "verified absent" half)."""
        engine, scratch_db = pg_scratch_engine
        admin, skip_reason = _pg_admin_engine()
        if admin is None:
            pytest.skip(skip_reason)
        with admin.connect() as conn:
            rows = list(
                conn.execute(
                    text(
                        "SELECT datname FROM pg_database WHERE datname = :db"
                    ),
                    {"db": scratch_db},
                )
            )
        admin.dispose()
        assert rows, "the scratch database must EXIST during the test"

        # Teardown runs after the test body — the fixture drops it.
        # (The drop + verification is the fixture's contract; this test
        # pins that the database was a REAL, reachable database.)
        engine.dispose()
