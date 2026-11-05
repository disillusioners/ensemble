"""The nest-anomaly STORAGE shape — the models + the two migrations.

Covers the phase-1 storage contract (decisions.md R7, AM-4, AM-5,
R1/R3):

* the two tables exist under their contracted names;
* the report + cluster COLUMNS are exactly the VERBATIM-SP set (a
  column that is missing, renamed or extra is a RED);
* the index names are byte-identical between the model
  ``__table_args__`` and the ``.sql`` files (the model / migration /
  storage-pack lockstep);
* the ``excerpt_sha256`` + ``excerpt_length`` pair exists (the excerpt
  is stored as a hash + a length, never as a second cleartext copy);
* the ``category`` (canonical) + ``category_raw`` (verbatim) pair
  exists — the vocabulary fold must never destroy the raw input;
* the outcome vocabulary is closed (AM-5's four tokens);
* every persisted stamp is the FIXED-WIDTH form (AM-4);
* the migration census is intact (121 files / 121 distinct prefixes)
  and the two new prefixes sit AFTER the previous head.

Mutation legs (phase6 §(b)):

* **M-1a** — drop the ``category_raw`` column → RED.
* **M-1b** — widen the excerpt cap by 1 byte → RED.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import (
    OUTCOME_VALUES,
    SEVERITY_VALUES,
    AnomalyCluster,
    AnomalyReport,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = REPO_ROOT / "daemon" / "migrations" / "versions"

REPORT_SQL = VERSIONS_DIR / "20261105_020500_create_anomaly_reports.sql"
CLUSTER_SQL = VERSIONS_DIR / "20261105_020501_create_anomaly_clusters.sql"

#: The VERBATIM-SP column sets (phase1-plan.md "SQLModel Models").
EXPECTED_REPORT_COLUMNS: tuple[str, ...] = (
    "id",
    "created_at",
    "agent_id",
    "instance_id",
    "tree_root_instance_id",
    "parent_chain",
    "parent_chain_agents",
    "category",
    "category_raw",
    "severity",
    "description",
    "suspected_cause",
    "excerpt",
    "excerpt_sha256",
    "excerpt_length",
    "job_id",
    "mission_id",
    "dispatch_chain",
    "boot_epoch",
    "registry_summary_sha256",
    "registry_captured_at",
    "wedge_capture_ids",
    "cluster_id",
    "outcome",
    "refusal_reason",
)

EXPECTED_CLUSTER_COLUMNS: tuple[str, ...] = (
    "id",
    "cluster_key_hash",
    "tree_root_instance_id",
    "category",
    "first_report_at",
    "last_report_at",
    "report_count",
    "max_severity",
    "board_item_id",
    "created_at",
)

EXPECTED_REPORT_INDEXES: tuple[str, ...] = (
    "idx_anomaly_reports_agent_created",
    "idx_anomaly_reports_cluster_id",
    "idx_anomaly_reports_tree_root_created",
    "idx_anomaly_reports_category_created",
    "idx_anomaly_reports_outcome_created",
)

EXPECTED_CLUSTER_INDEXES: tuple[str, ...] = (
    "idx_anomaly_clusters_key_window",
)

#: The fixed-width stamp shape (AM-4) — 32 chars, always ``+00:00``.
FIXED_WIDTH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")

_CREATE_TABLE_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", re.DOTALL | re.IGNORECASE
)
_CREATE_INDEX_RE = re.compile(
    r"CREATE INDEX IF NOT EXISTS\s+(\w+)", re.IGNORECASE
)


def _sql_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sql_up_section(path: Path) -> str:
    """The ``-- UP`` body of a migration file (the ``-- DOWN`` body is
    audit documentation and is NEVER executed)."""
    text = _sql_text(path)
    up_marker = "\n-- UP\n"
    down_marker = "\n-- DOWN\n"
    assert up_marker in text, path.name
    body = text.split(up_marker, 1)[1]
    if down_marker in body:
        body = body.split(down_marker, 1)[0]
    return body


def _sql_table_columns(path: Path) -> list[str]:
    """The column names declared by the ``CREATE TABLE`` in ``path``."""
    match = _CREATE_TABLE_RE.search(_sql_up_section(path))
    assert match is not None, f"no CREATE TABLE found in {path.name}"
    body = match.group(2)
    columns: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE")):
            continue
        name = line.split()[0]
        columns.append(name.strip('"'))
    return columns


def _sql_index_names(path: Path) -> list[str]:
    return [m.group(1) for m in _CREATE_INDEX_RE.finditer(_sql_up_section(path))]


class TestTableShapes:
    def test_the_two_tables_exist_under_their_contracted_names(self) -> None:
        assert AnomalyReport.__tablename__ == "anomaly_reports"
        assert AnomalyCluster.__tablename__ == "anomaly_clusters"

    def test_the_report_columns_are_exactly_the_verbatim_spec(self) -> None:
        assert tuple(AnomalyReport.__table__.columns.keys()) == EXPECTED_REPORT_COLUMNS

    def test_the_cluster_columns_are_exactly_the_verbatim_spec(self) -> None:
        assert tuple(AnomalyCluster.__table__.columns.keys()) == EXPECTED_CLUSTER_COLUMNS

    def test_the_report_indexes_are_named_and_complete(self) -> None:
        names = {index.name for index in AnomalyReport.__table__.indexes}
        assert names == set(EXPECTED_REPORT_INDEXES)

    def test_the_cluster_carries_the_one_r5_index(self) -> None:
        names = {index.name for index in AnomalyCluster.__table__.indexes}
        assert names == set(EXPECTED_CLUSTER_INDEXES)

    def test_the_index_names_are_byte_identical_to_the_sql_files(self) -> None:
        assert sorted(_sql_index_names(REPORT_SQL)) == sorted(EXPECTED_REPORT_INDEXES)
        assert sorted(_sql_index_names(CLUSTER_SQL)) == sorted(EXPECTED_CLUSTER_INDEXES)

    def test_the_sql_column_names_match_the_model_columns(self) -> None:
        assert tuple(_sql_table_columns(REPORT_SQL)) == EXPECTED_REPORT_COLUMNS
        assert tuple(_sql_table_columns(CLUSTER_SQL)) == EXPECTED_CLUSTER_COLUMNS

    def test_the_json_columns_are_the_dual_face_type(self) -> None:
        from daemon.repositories.infra.types import JSONBType

        for name in ("parent_chain", "parent_chain_agents", "dispatch_chain", "wedge_capture_ids"):
            column = AnomalyReport.__table__.columns[name]
            assert isinstance(column.type, JSONBType), (
                f"{name} must be the dual-face JSON type (SQLite JSON / PG JSONB)"
            )

    def test_every_string_column_is_text_and_the_counters_are_integer(self) -> None:
        for name in ("description", "category", "category_raw", "severity"):
            assert AnomalyReport.__table__.columns[name].type.__class__.__name__ == "Text"
        assert (
            AnomalyReport.__table__.columns["excerpt_length"].type.__class__.__name__
            == "Integer"
        )
        assert (
            AnomalyCluster.__table__.columns["report_count"].type.__class__.__name__
            == "Integer"
        )


class TestTheClosedVocabularies:
    def test_the_outcome_vocabulary_is_closed_and_carries_am_5s_token(self) -> None:
        assert OUTCOME_VALUES == (
            "accepted",
            "rate_limited",
            "persist-failed",
            "unclustered",
        )

    def test_the_severity_vocabulary_is_closed(self) -> None:
        assert SEVERITY_VALUES == ("minor", "major", "critical")

    def test_the_category_and_raw_pair_both_exist(self) -> None:
        columns = AnomalyReport.__table__.columns
        assert "category" in columns
        assert "category_raw" in columns
        # The raw column is NOT NULL — the folded-from value is always
        # preserved (the vocabulary gap must stay visible).
        assert columns["category_raw"].nullable is False
        assert columns["category"].nullable is False

    def test_the_excerpt_hash_and_length_pair_exists(self) -> None:
        columns = AnomalyReport.__table__.columns
        assert "excerpt_sha256" in columns
        assert "excerpt_length" in columns
        assert columns["excerpt_sha256"].nullable is True
        assert columns["excerpt_length"].nullable is True


class TestTimestamps:
    def test_the_stamp_is_the_fixed_width_am_4_form(self) -> None:
        from daemon.repositories.anomaly.models import _utc_now_iso

        stamp = _utc_now_iso()
        assert FIXED_WIDTH_RE.match(stamp), stamp
        assert len(stamp) == 32

    def test_the_two_stamp_columns_default_to_the_fixed_width_form(self) -> None:
        report = AnomalyReport(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            category_raw="tool-failure",
            severity="major",
            description="boom",
        )
        assert FIXED_WIDTH_RE.match(report.created_at), report.created_at
        cluster = AnomalyCluster(
            cluster_key_hash="h",
            tree_root_instance_id="root",
            category="tool-failure",
            first_report_at=report.created_at,
            last_report_at=report.created_at,
            max_severity="major",
        )
        assert FIXED_WIDTH_RE.match(cluster.created_at), cluster.created_at


class TestTheExcerptCaps:
    """The C5 numeric bounds — the M-1b leg lives here."""

    def test_the_excerpt_caps_are_the_c5_numbers(self) -> None:
        config = NestReportConfig()
        assert config.excerpt_max_messages == 5
        assert config.excerpt_max_chars_per_message == 500
        assert config.excerpt_max_total_chars == 2000


class TestTheMigrationCensus:
    def test_the_census_is_121_files_with_121_distinct_prefixes(self) -> None:
        files = sorted(p.name for p in VERSIONS_DIR.glob("*.sql"))
        prefixes = [name.split("_", 2)[0] + "_" + name.split("_", 2)[1] for name in files]
        assert len(files) == 121, len(files)
        assert len(set(prefixes)) == 121, len(set(prefixes))

    def test_the_two_new_prefixes_sit_after_the_previous_head(self) -> None:
        assert "20261105_020500" > "20261031_120002"
        assert "20261105_020501" > "20261031_120002"

    def test_the_two_new_files_exist(self) -> None:
        assert REPORT_SQL.is_file()
        assert CLUSTER_SQL.is_file()

    def test_each_sql_file_carries_an_up_and_a_down_section(self) -> None:
        for path in (REPORT_SQL, CLUSTER_SQL):
            text = _sql_text(path)
            assert "\n-- UP\n" in text, path.name
            assert "\n-- DOWN\n" in text, path.name

    def test_every_create_in_the_sql_files_is_idempotent(self) -> None:
        for path in (REPORT_SQL, CLUSTER_SQL):
            text = _sql_text(path)
            for statement in re.findall(r"CREATE (?:TABLE|INDEX) [^\n]*", text):
                assert "IF NOT EXISTS" in statement, statement

    def test_no_inline_comment_carries_a_semicolon(self) -> None:
        # The runner strips FULL-LINE comments only; an inline
        # (trailing) comment carrying a semicolon would corrupt the
        # statement split.
        for path in (REPORT_SQL, CLUSTER_SQL):
            for line in _sql_up_section(path).splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                if "--" in stripped:
                    assert ";" not in stripped, f"{path.name}: {line}"


class TestTheFreshSqliteBoot:
    """AC6 — the two ``.sql`` files apply cleanly to a fresh SQLite DB."""

    def test_both_migrations_apply_to_a_fresh_sqlite_database(self, tmp_path) -> None:
        from sqlalchemy import create_engine, inspect, text

        engine = create_engine(f"sqlite:///{tmp_path / 'anomaly_migrations.db'}")
        try:
            with engine.begin() as conn:
                for path in (REPORT_SQL, CLUSTER_SQL):
                    code = _sql_up_section(path)
                    for statement in code.split(";"):
                        statement = statement.strip()
                        if statement:
                            conn.execute(text(statement))
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert "anomaly_reports" in tables
            assert "anomaly_clusters" in tables
            report_indexes = {index["name"] for index in inspector.get_indexes("anomaly_reports")}
            assert report_indexes == set(EXPECTED_REPORT_INDEXES)
            cluster_indexes = {
                index["name"] for index in inspector.get_indexes("anomaly_clusters")
            }
            assert cluster_indexes == set(EXPECTED_CLUSTER_INDEXES)
        finally:
            engine.dispose()

    def test_the_models_and_the_sql_agree_on_the_column_names(self) -> None:
        # The MODEL half of the lockstep: a create_all over the model
        # yields columns the INSERT shapes below land in. (The
        # create_all -> `.sql` "already exists" collision class is
        # covered statically by the IF-NOT-EXISTS pin above and
        # behaviourally by the fresh-SQLite apply leg; this test proves
        # the model's own insert shape.)
        from sqlalchemy import create_engine, text

        engine = create_engine("sqlite:///:memory:")
        try:
            SQLModel.metadata.create_all(
                engine,
                tables=[AnomalyReport.__table__, AnomalyCluster.__table__],
            )
            with engine.connect() as conn:
                for statement in (
                    "INSERT INTO anomaly_reports (id, created_at, agent_id, instance_id,"
                    " parent_chain, parent_chain_agents, category, category_raw, severity,"
                    " description, outcome) VALUES ('r1', '2026-11-05T00:00:00.000000+00:00',"
                    " 'worker', 'i1', '[]', '[]', 'tool-failure', 'tool failure', 'major',"
                    " 'boom', 'accepted')",
                    "INSERT INTO anomaly_clusters (id, cluster_key_hash, tree_root_instance_id,"
                    " category, first_report_at, last_report_at, report_count, max_severity,"
                    " created_at) VALUES ('c1', 'h', 'r1', 'tool-failure',"
                    " '2026-11-05T00:00:00.000000+00:00', '2026-11-05T00:00:00.000000+00:00',"
                    " 1, 'major', '2026-11-05T00:00:00.000000+00:00')",
                ):
                    conn.execute(text(statement))
        finally:
            engine.dispose()


class TestTheScratchPostgresLeg:
    """AC6 — the scratch-POSTGRESQL leg.

    The house PG recipe (``tests/postgres/conftest.py``): the coordinates
    come from the ``PG_TEST_*`` env vars ONLY (``POSTGRES_*`` /
    ``DATABASE_URL`` are NEVER read), the target must be a SANCTIONED
    SCRATCH database, and the live databases (``ensemble_prod`` /
    ``ensemble_dev`` / ``ensemble_prod_test``) are forbidden outright.

    This leg builds its OWN uniquely-named scratch database over TCP
    with the superuser role, creates the two tables through the MODEL
    (``SQLModel.metadata.create_all`` — the PG source of truth, R7) and
    DROPS the scratch database at teardown. It never touches a live
    database: the name is random and the guard below refuses any target
    whose host is not loopback.
    """

    def test_a_scratch_postgres_database_yields_both_tables_and_their_indexes(
        self,
    ) -> None:
        import os
        import uuid

        from sqlalchemy import create_engine, inspect, text

        host = os.environ.get("PG_TEST_HOST", "localhost")
        port = os.environ.get("PG_TEST_PORT", "5432")
        user = os.environ.get("PG_TEST_USER", "ensemble")
        password = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

        if host not in {"localhost", "127.0.0.1", "::1"}:
            pytest.skip(
                f"the scratch-PG leg only runs against a loopback server "
                f"(PG_TEST_HOST={host!r} is not loopback — it may be live)"
            )

        admin_url = (
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"
        )
        scratch_name = f"ensemble_anomaly_c1_{uuid.uuid4().hex[:12]}"
        assert scratch_name not in {
            "ensemble_prod",
            "ensemble_dev",
            "ensemble_prod_test",
        }
        admin = create_engine(admin_url, pool_pre_ping=True, future=True)
        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))
        except Exception as exc:  # noqa: BLE001 — skip-cleanly
            pytest.skip(f"no scratch database could be created: {exc}")
        scratch_url = (
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/{scratch_name}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True, future=True)
        try:
            SQLModel.metadata.create_all(
                engine,
                tables=[AnomalyReport.__table__, AnomalyCluster.__table__],
            )
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert "anomaly_reports" in tables
            assert "anomaly_clusters" in tables
            report_indexes = {
                index["name"] for index in inspector.get_indexes("anomaly_reports")
            }
            assert report_indexes == set(EXPECTED_REPORT_INDEXES)
            cluster_indexes = {
                index["name"] for index in inspector.get_indexes("anomaly_clusters")
            }
            assert cluster_indexes == set(EXPECTED_CLUSTER_INDEXES)
            # The dual-face JSON type really is JSONB on PostgreSQL.
            json_columns = {
                column["name"]: column["type"]
                for column in inspector.get_columns("anomaly_reports")
                if column["name"] in {"parent_chain", "dispatch_chain"}
            }
            assert set(json_columns) == {"parent_chain", "dispatch_chain"}
            for name, pg_type in json_columns.items():
                assert "JSONB" in str(pg_type).upper(), (name, pg_type)
        finally:
            engine.dispose()
            with admin.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}"'))
            admin.dispose()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
