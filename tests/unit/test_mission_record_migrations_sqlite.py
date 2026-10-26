"""Migration-runner tests — the mission-record PR1 migrations apply.

The SQLite path is the migration runner; this file proves the three PR1
migration files apply cleanly on a fresh SQLite database (the runner's
real code path — not create_all) and that the resulting schema carries
the tables, the composite indexes, the PARTIAL unique mint-idempotency
index, and the three additive provenance columns.

Dual-driver contract reminder (README + blueprint): on PostgreSQL the
runner is a NO-OP — brand-new tables come from ``create_all`` (the
models are registered before create_all at manager boot) and the three
additive columns come from the idempotent
``_ensure_postgres_columns`` statements (mirrored verbatim from the
``.sql`` file).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.pool import NullPool


@pytest.fixture
def bare_sqlite(tmp_path: Path):
    """A bare SQLite engine with ONLY the migrations applied (no
    create_all) — the pre-mission-records baseline needs the two host
    tables to exist first, so apply the migrations IN ORDER up to and
    including ours via the real MigrationRunner."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'mission-migrations.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    yield eng
    eng.dispose()


MISSION_MIGRATIONS = (
    "20261025_122631_create_mission_records.sql",
    "20261025_122700_create_mission_commissions.sql",
    "20261025_122730_add_mission_provenance_columns.sql",
    # PR3 (OQ-3(b)) — the commission RESOLUTION stamp.
    "20261026_060000_add_mission_commission_resolved_at.sql",
)

#: The canonical migrations directory (hoisted — the three sites below
#: used to recompute the same path expression).
VERSIONS_DIR = (
    Path(__file__).resolve().parents[2] / "daemon" / "migrations" / "versions"
)


def _apply_mission_migrations(engine) -> list[str]:
    """Apply ONLY the three PR1 migrations (the runner's real code path).

    ``run_pending_migrations`` replays the WHOLE history from the
    2024 baseline (needing every host table); a bare in-test DB only
    needs the two host tables the additive migration ALTERs, so the
    three files are applied individually through
    ``MigrationRunner.apply_migration`` (the
    ``test_ensure_deferred_schema_pin`` pattern).
    """
    from daemon.migrations.runner import MigrationFile, MigrationRunner

    versions_dir = VERSIONS_DIR
    runner = MigrationRunner(engine)
    runner.ensure_migrations_table()  # the ledger (run_pending normally does this)
    applied = []
    for name in MISSION_MIGRATIONS:
        migration = MigrationFile.parse(versions_dir / name)
        runner.apply_migration(migration)
        applied.append(migration.version)
    return applied


def _ensure_host_tables(engine) -> None:
    """The two host tables the additive-columns migration ALTERs.

    On a REAL deployment these exist long before the mission migrations
    (created by create_all / earlier migrations). A bare in-test DB has
    neither, so recreate their minimal pre-PR1 shape here — the exact
    column set the migration's ALTER TABLE statements target.
    """
    # Use the REAL models minus the new column: create via raw DDL so
    # the migration's ADD COLUMN is genuinely exercised (create_all
    # would already include mission_id and make the ALTER a no-op).
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS report_injections ("
                "injection_id TEXT PRIMARY KEY, "
                "parent_instance_id TEXT NOT NULL, "
                "child_instance_id TEXT NOT NULL, "
                "child_message_id TEXT NOT NULL, "
                "report_message_id TEXT, "
                "content TEXT, "
                "created_at TEXT NOT NULL, "
                "delivered_at TEXT, "
                "state TEXT NOT NULL DEFAULT 'PENDING', "
                "deferred_reason TEXT, "
                "recovery_attempted_at TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS message_metadata ("
                "thread_id TEXT NOT NULL, "
                "message_id TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "seq INTEGER, "
                "PRIMARY KEY (thread_id, message_id))"
            )
        )


def test_mission_migrations_apply_and_land_schema(bare_sqlite):
    _ensure_host_tables(bare_sqlite)
    applied = _apply_mission_migrations(bare_sqlite)
    assert applied == [
        "20261025_122631",
        "20261025_122700",
        "20261025_122730",
        "20261026_060000",
    ]

    inspector = inspect(bare_sqlite)

    # ── mission_records ──
    assert inspector.has_table("mission_records")
    mr_cols = {c["name"] for c in inspector.get_columns("mission_records")}
    assert {
        "mission_id",
        "anchor_instance_id",
        "epoch_seq",
        "founding_message_id",
        "founding_source",
        "commissioner_instance_id",
        "work_id",
        "parent_mission_id",
        "status",
        "attested_at",
        "attest_method",
        "attestation_bypassed",
        "created_at",
        "settled_at",
        "provenance",
    } <= mr_cols
    mr_indexes = {i["name"]: i for i in inspector.get_indexes("mission_records")}
    assert "ix_mission_records_anchor_status" in mr_indexes
    ux = mr_indexes.get("ux_mission_records_anchor_founding_msg")
    assert ux is not None and bool(ux.get("unique")) is True

    # ── mission_commissions ──
    assert inspector.has_table("mission_commissions")
    mc_cols = {c["name"] for c in inspector.get_columns("mission_commissions")}
    assert {
        "commission_id",
        "mission_id",
        "commissioner_instance_id",
        "worker_instance_id",
        "work_id",
        "lane",
        "state",
        "created_at",
        "satisfied_at",
        "abandon_reason",
        "advisory_backfill",
        # PR3 (OQ-3(b)) — the resolution stamp.
        "resolved_at",
    } <= mc_cols
    mc_indexes = {i["name"] for i in inspector.get_indexes("mission_commissions")}
    assert "ix_mission_commissions_mission_state" in mc_indexes
    assert "ix_mission_commissions_worker_state" in mc_indexes

    # ── the three additive provenance columns ──
    ri_cols = {
        c["name"] for c in inspector.get_columns("report_injections")
    }
    assert "mission_id" in ri_cols
    assert "commissioner_instance_id" in ri_cols
    mm_cols = {c["name"] for c in inspector.get_columns("message_metadata")}
    assert "mission_id" in mm_cols


def test_mission_migrations_are_idempotent_on_reapply(bare_sqlite):
    _ensure_host_tables(bare_sqlite)
    _apply_mission_migrations(bare_sqlite)
    # The ledger marks all three applied → a second pending-pass skips
    # them (the runner's pending filter, not apply_migration's raw
    # ledger insert). Schema-level idempotency is proven separately by
    # re-executing the UP SQL (every statement is IF NOT EXISTS or a
    # gracefully-skipped duplicate).
    from daemon.migrations.runner import MigrationFile, MigrationRunner

    versions_dir = VERSIONS_DIR
    runner = MigrationRunner(bare_sqlite)
    status = runner.get_migration_status()
    applied = set(status["applied"])
    assert {
        "20261025_122631",
        "20261025_122700",
        "20261025_122730",
        "20261026_060000",
    } <= applied

    # Raw-SQL idempotency: replay the UP sections directly — every
    # statement survives a duplicate (IF NOT EXISTS / duplicate-column
    # tolerance is the runner's own contract). Comment-stripping before
    # the split mirrors the runner's own defensive statement splitter
    # (in-comment semicolons must not break boundaries).
    for name in MISSION_MIGRATIONS:
        up_sql = (
            (versions_dir / name).read_text().split("-- UP", 1)[1]
            .split("-- DOWN", 1)[0]
        )
        code_lines = [
            ln for ln in up_sql.splitlines()
            if not ln.lstrip().startswith("--")
        ]
        statements = [
            s_.strip() for s_ in "\n".join(code_lines).split(";") if s_.strip()
        ]
        with bare_sqlite.begin() as conn:
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except Exception as exc:  # noqa: BLE001
                    assert "duplicate column name" in str(exc), (name, exc)
    inspector = inspect(bare_sqlite)
    assert inspector.has_table("mission_records")
    assert inspector.has_table("mission_commissions")


def test_the_boolean_columns_match_the_model_spelling(bare_sqlite):
    """TASK C (PR3 carry-over) — the .sql DDL matches the MODELS.

    The models are the truth: ``advisory_backfill`` /
    ``attestation_bypassed`` are ``bool`` (SQLModel ``Boolean``), so
    the SQLite migrations must spell them ``BOOLEAN`` (they said
    ``INTEGER`` — the PR1-era drift). The VALUES are identical (0/1);
    this is a declaration pin.
    """
    _ensure_host_tables(bare_sqlite)
    _apply_mission_migrations(bare_sqlite)
    inspector = inspect(bare_sqlite)
    mc_types = {
        c["name"]: str(c["type"]).upper()
        for c in inspector.get_columns("mission_commissions")
    }
    mr_types = {
        c["name"]: str(c["type"]).upper()
        for c in inspector.get_columns("mission_records")
    }
    assert mc_types["advisory_backfill"] == "BOOLEAN"
    assert mr_types["attestation_bypassed"] == "BOOLEAN"


def test_the_resolution_stamp_migration_matches_the_model(bare_sqlite):
    """The 3-site pin for the PR3 ``resolved_at`` column.

    The SQLite migration (this file), the SQLModel field
    (``MissionCommission.resolved_at``) and the PostgreSQL
    ``_ensure_postgres_columns`` mirror must all carry the column.
    """
    _ensure_host_tables(bare_sqlite)
    _apply_mission_migrations(bare_sqlite)
    inspector = inspect(bare_sqlite)
    mc_cols = {
        c["name"] for c in inspector.get_columns("mission_commissions")
    }
    assert "resolved_at" in mc_cols

    from daemon.repositories.mission_record.models import MissionCommission

    assert "resolved_at" in MissionCommission.model_fields

    manager_source = (
        Path(__file__).resolve().parents[2]
        / "daemon"
        / "manager.py"
    ).read_text(encoding="utf-8")
    assert (
        "ALTER TABLE mission_commissions ADD COLUMN IF NOT EXISTS resolved_at TEXT"
        in manager_source
    )


def test_partial_unique_index_enforces_mint_idempotency(bare_sqlite):
    """The DATABASE-level mint-storm guard: two rows sharing
    (anchor, founding_message_id) violate the partial unique index;
    NULL founding messages are exempt (boot/backfill honest nulls)."""
    from sqlalchemy.exc import IntegrityError

    _ensure_host_tables(bare_sqlite)
    _apply_mission_migrations(bare_sqlite)
    with bare_sqlite.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mission_records (mission_id, anchor_instance_id,"
                " epoch_seq, founding_message_id, founding_source, status,"
                " created_at) VALUES ('m1','a1',1,'msg-1','user_api','open','t')"
            )
        )
        # same (anchor, founding_message_id) → rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO mission_records (mission_id,"
                    " anchor_instance_id, epoch_seq, founding_message_id,"
                    " founding_source, status, created_at) VALUES"
                    " ('m2','a1',2,'msg-1','chat','open','t')"
                )
            )
    # NULL founding rows are exempt (the partial predicate)
    with bare_sqlite.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mission_records (mission_id, anchor_instance_id,"
                " epoch_seq, founding_message_id, founding_source, status,"
                " created_at) VALUES ('m3','a1',3,NULL,'boot','open','t')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO mission_records (mission_id, anchor_instance_id,"
                " epoch_seq, founding_message_id, founding_source, status,"
                " created_at) VALUES ('m4','a1',4,NULL,'boot','open','t')"
            )
        )


def test_rollback_safety_no_destructive_ddl():
    """Rollback-safety derivation (full-history DROP-scan): the three
    PR1 migrations' UP sections contain NO DROP TABLE / DROP COLUMN —
    the release stays rollback_safe without an author override."""
    versions_dir = VERSIONS_DIR
    for name in MISSION_MIGRATIONS:
        up = (
            versions_dir.joinpath(name)
            .read_text()
            .split("-- UP", 1)[1]
            .split("-- DOWN", 1)[0]
            .upper()
        )
        assert "DROP TABLE" not in up, name
        assert "DROP COLUMN" not in up, name
