"""POK write-gate LEDGER — schema, repository and read-surface pins.

Covers pok-write-gate Phase 1 T1.1 (the ledger table + the merge-
proposal fields landing UP FRONT), T1.2 (the additive migration),
T1.3 (the append-only repository), T1.4 (the W1 half — the schema and
the repo accept the 3-row ASK chain shape) and T1.7 (the ledger read
surface).

Fence discipline: throwaway in-memory SQLite engines only. No daemon
boot, no InstanceManager, no live store, no network, no PostgreSQL.
The migration is applied to a THROWAWAY SQLite file, never to a live
database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine, inspect
from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import SQLModel

from daemon.repositories.pok.models import (
    POK_ARCHIVE_BASES,
    POK_LEDGER_MERGE_KINDS,
    POK_LEDGER_PHASES,
    POK_LEDGER_SOURCES,
    POK_LEDGER_VERDICTS,
    POKWriteGateLedger,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.repositories.pok.repository import POKRepository

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = REPO_ROOT / "daemon" / "migrations" / "versions"
LEDGER_SQL = VERSIONS_DIR / "20261124_060000_create_pok_write_gate_ledger.sql"

#: The 7 merge-proposal fields the plan's §13.5 "no-pick-foreclosed
#: rule" requires UP FRONT — a schema that cannot represent a
#: proposal forces a migration at P1.
MERGE_PROPOSAL_FIELDS = (
    "target_slug",
    "merge_kind",
    "proposed_body",
    "proposed_body_sha256",
    "target_body_sha256",
    "executed_body_sha256",
    "executed_by",
)

#: The 5 record-kind fields the ledger must carry (phase1-plan.md
#: T1.1): verdict, reason, judge_explanation, executed, basis.
REQUIRED_RECORD_FIELDS = (
    "verdict",       # the gate's outcome (admission decision)
    "reason",        # the caller's operational claim (the excerpt)
    "judge_explanation",
    "executed",      # the tool's execution
    "basis",         # the ruled archive basis
)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        eng,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
            POKWriteGateLedger.__table__,
        ],
    )
    return eng


@pytest.fixture()
def repo(engine) -> POKRepository:
    return POKRepository(engine)


# ── T1.1 — the schema ───────────────────────────────────────────────────


class TestLedgerSchema:
    def test_table_name_and_registration(self) -> None:
        assert POKWriteGateLedger.__tablename__ == "pok_write_gate_ledger"
        # The manager's late-import block imports the POK models
        # MODULE before create_all, so a table defined in this module
        # is registered without any manager.py edit.
        assert "pok_write_gate_ledger" in SQLModel.metadata.tables

    def test_gate_id_and_phase_columns_exist(self) -> None:
        """T1.4 (W1 half): the schema carries ``gate_id`` + ``phase``."""
        cols = set(POKWriteGateLedger.__table__.columns.keys())
        assert "gate_id" in cols
        assert "phase" in cols

    def test_merge_proposal_fields_land_up_front(self) -> None:
        """design-amendments-v2 §13.5 — the 7 proposal fields."""
        cols = set(POKWriteGateLedger.__table__.columns.keys())
        missing = [f for f in MERGE_PROPOSAL_FIELDS if f not in cols]
        assert not missing, f"merge-proposal fields missing: {missing}"

    def test_all_five_record_kinds_are_representable(self) -> None:
        """verdict + reason + judge_explanation + executed + basis."""
        cols = set(POKWriteGateLedger.__table__.columns.keys())
        missing = [f for f in REQUIRED_RECORD_FIELDS if f not in cols]
        assert not missing, f"record-kind fields missing: {missing}"

    def test_phase_vocabulary_covers_the_three_row_ask_chain(self) -> None:
        assert "ASK_MINTED" in POK_LEDGER_PHASES
        assert "ASK_ANSWERED" in POK_LEDGER_PHASES
        assert "ASK_EXECUTED" in POK_LEDGER_PHASES
        # The plain chain's two rows + the retrieval kind.
        assert "ADMISSION" in POK_LEDGER_PHASES
        assert "EXECUTION" in POK_LEDGER_PHASES
        assert "RETRIEVAL" in POK_LEDGER_PHASES

    def test_verdict_merge_kind_and_basis_vocabularies(self) -> None:
        assert POK_LEDGER_VERDICTS == ("ACCEPT", "MERGE", "ASK", "REJECT")
        assert POK_LEDGER_MERGE_KINDS == ("no_delta", "delta")
        # The three ruled bases the plan names, verbatim.
        assert "covered-by-surviving-row" in POK_ARCHIVE_BASES
        assert "relocated-to-routed-home" in POK_ARCHIVE_BASES
        assert "NOW-FALSE" in POK_ARCHIVE_BASES

    def test_source_vocabulary_disambiguates_query_vs_injection(self) -> None:
        assert POK_LEDGER_SOURCES == ("injection", "query")
        assert "source" in POKWriteGateLedger.__table__.columns.keys()


# ── T1.2 — the migration ───────────────────────────────────────────────


def _up_section(sql: str) -> str:
    return sql.split("-- UP", 1)[1].split("-- DOWN", 1)[0]


def _sql_columns(sql_path: Path, table: str) -> dict[str, dict[str, object]]:
    up = _up_section(sql_path.read_text())
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\((.*?)\)\s*;",
        up,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"could not parse CREATE TABLE for {table}"
    cols: dict[str, dict[str, object]] = {}
    for raw in match.group(1).splitlines():
        line = raw.split("--", 1)[0].strip().rstrip(",")
        if not line:
            continue
        tokens = line.split()
        cols[tokens[0]] = {
            "type": tokens[1].upper(),
            "not_null": "NOT NULL" in line.upper(),
            "primary_key": "PRIMARY KEY" in line.upper(),
        }
    return cols


def _model_kind(column) -> str:
    t = column.type
    if isinstance(t, Text):
        return "text"
    if isinstance(t, Boolean):
        return "boolean"
    if isinstance(t, Float):
        return "float"
    if isinstance(t, Integer):
        return "integer"
    if isinstance(t, String):
        return "varchar"
    raise AssertionError(f"{column.name}: unmapped model column type {t!r}")


#: The sanctioned dual-driver pairings — the ONLY places a model type
#: deliberately differs from the ``.sql`` token (Float compiles to
#: FLOAT on both dialects; the SQLite-valid DDL spells the same
#: storage class REAL).
_SQL_TOKEN_BY_MODEL_KIND = {
    "text": "TEXT",
    "boolean": "BOOLEAN",
    "float": "REAL",
    "integer": "INTEGER",
    "varchar": "VARCHAR",
}


class TestLedgerMigration:
    def test_the_migration_file_exists(self) -> None:
        assert LEDGER_SQL.is_file()
        assert LEDGER_SQL.name == "20261124_060000_create_pok_write_gate_ledger.sql"

    def test_up_section_is_additive_and_idempotent(self) -> None:
        up = _up_section(LEDGER_SQL.read_text())
        assert "CREATE TABLE IF NOT EXISTS pok_write_gate_ledger" in up
        # Every CREATE carries IF NOT EXISTS (the boot-order guard).
        creates = re.findall(r"CREATE (TABLE|INDEX)(?! IF NOT EXISTS)", up)
        assert not creates, f"non-idempotent CREATE in the UP section: {creates}"
        # Additive-only: the UP section touches no pre-existing table.
        assert "ALTER TABLE" not in up
        assert "DROP TABLE" not in up
        assert "UPDATE " not in up.upper()

    def test_migration_touches_no_pok_or_embeddings_table(self) -> None:
        """Success criterion #18 — the ledger migration is the ONLY new
        ``.sql`` and it touches NO POK/embeddings table."""
        up = _up_section(LEDGER_SQL.read_text())
        assert "project_operating_knowledge" not in up
        assert "embeddings" not in up

    def test_down_section_drops_only_the_ledger(self) -> None:
        down = LEDGER_SQL.read_text().split("-- DOWN", 1)[1]
        assert "DROP TABLE IF EXISTS pok_write_gate_ledger" in down
        assert "project_operating_knowledge" not in down

    def test_column_names_match_the_model(self) -> None:
        sql_cols = _sql_columns(LEDGER_SQL, "pok_write_gate_ledger")
        model_cols = {c.name for c in POKWriteGateLedger.__table__.columns}
        assert model_cols == set(sql_cols)

    def test_column_types_and_nullability_match_the_model(self) -> None:
        sql_cols = _sql_columns(LEDGER_SQL, "pok_write_gate_ledger")
        for column in POKWriteGateLedger.__table__.columns:
            spec = sql_cols[column.name]
            assert spec["type"] == _SQL_TOKEN_BY_MODEL_KIND[_model_kind(column)], (
                f"{column.name}: .sql token {spec['type']} != model kind"
            )
            # A PRIMARY KEY counts as NOT NULL (SQLite implies it).
            sql_not_null = bool(spec["not_null"]) or bool(spec["primary_key"])
            assert column.nullable is (not sql_not_null), column.name

    def test_types_compile_on_both_dialects(self) -> None:
        for column in POKWriteGateLedger.__table__.columns:
            for dialect in (sqlite.dialect(), postgresql.dialect()):
                column.type.compile(dialect=dialect)

    def test_index_lockstep_between_model_and_migration(self) -> None:
        eng = create_engine("sqlite://")
        SQLModel.metadata.create_all(eng, tables=[POKWriteGateLedger.__table__])
        model_idx = {
            i["name"]: tuple(i["column_names"])
            for i in inspect(eng).get_indexes("pok_write_gate_ledger")
        }
        up = _up_section(LEDGER_SQL.read_text())
        sql_idx = {
            name: tuple(c.strip() for c in cols.split(","))
            for name, cols in re.findall(
                r"CREATE INDEX IF NOT EXISTS (\S+)\s*\n\s*ON "
                r"pok_write_gate_ledger \(([^)]+)\)",
                up,
            )
        }
        assert model_idx == sql_idx

    def test_check_constraints_are_mirrored_in_both_sides(self) -> None:
        up = _up_section(LEDGER_SQL.read_text())
        assert up.count("CHECK (") == 5  # phase, verdict, merge_kind, basis, source
        model_checks = {
            c.name
            for c in POKWriteGateLedger.__table__.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }
        assert model_checks == {
            "ck_pok_ledger_phase",
            "ck_pok_ledger_verdict",
            "ck_pok_ledger_merge_kind",
            "ck_pok_ledger_basis",
            "ck_pok_ledger_source",
        }


# ── T1.3 + T1.7 — the repository surface ───────────────────────────────


def _row(repo: POKRepository, **kwargs) -> POKWriteGateLedger:
    base = {"project_id": "p1", "phase": "ADMISSION"}
    base.update(kwargs)
    return repo.append_ledger(POKWriteGateLedger(**base))


class TestLedgerRepository:
    def test_append_then_query(self, repo: POKRepository) -> None:
        _row(repo, phase="ADMISSION", verdict="ACCEPT", reason="r")
        _row(repo, phase="EXECUTION", verdict="ACCEPT", executed=True)
        rows = repo.query_ledger("p1")
        assert len(rows) == 2
        assert {r.phase for r in rows} == {"ADMISSION", "EXECUTION"}

    def test_append_is_scoped_by_project(self, repo: POKRepository) -> None:
        _row(repo, project_id="p1")
        _row(repo, project_id="p2")
        assert len(repo.query_ledger("p1")) == 1
        assert len(repo.query_ledger("p2")) == 1

    def test_query_filters_by_gate_phase_entry_and_source(
        self, repo: POKRepository
    ) -> None:
        _row(repo, gate_id="g1", phase="ASK_MINTED", verdict="ASK")
        _row(repo, gate_id="g1", phase="ASK_ANSWERED", answer_excerpt="yes")
        _row(repo, gate_id="g2", phase="ADMISSION", entry_id="e1")
        _row(repo, phase="RETRIEVAL", entry_id="e1", source="injection")
        assert len(repo.query_ledger("p1", gate_id="g1")) == 2
        assert len(repo.query_ledger("p1", phase="RETRIEVAL")) == 1
        assert len(repo.query_ledger("p1", entry_id="e1")) == 2
        assert len(repo.query_ledger("p1", source="injection")) == 1
        assert len(repo.query_ledger("p1", source="query")) == 0

    def test_gate_chain_reads_oldest_first(self, repo: POKRepository) -> None:
        for phase in ("ASK_MINTED", "ASK_ANSWERED", "ASK_EXECUTED"):
            _row(repo, gate_id="g1", phase=phase)
        chain = repo.ledger_gate_chain("p1", "g1")
        assert [r.phase for r in chain] == [
            "ASK_MINTED",
            "ASK_ANSWERED",
            "ASK_EXECUTED",
        ]

    def test_count_ledger_phase(self, repo: POKRepository) -> None:
        _row(repo, phase="RETRIEVAL", source="injection")
        _row(repo, phase="RETRIEVAL", source="injection")
        _row(repo, phase="ADMISSION")
        assert repo.count_ledger_phase("p1", "RETRIEVAL") == 2
        assert repo.count_ledger_phase("p1", "ADMISSION") == 1

    def test_the_surface_has_no_update_or_delete(self, repo: POKRepository) -> None:
        """APPEND-ONLY: a mutable audit is not an audit."""
        forbidden = [
            name
            for name in dir(repo)
            if name.startswith(("update_ledger", "delete_ledger", "mutate_ledger"))
        ]
        assert not forbidden

    def test_limit_bounds_the_read(self, repo: POKRepository) -> None:
        for i in range(5):
            _row(repo, phase="ADMISSION", reason=f"r{i}")
        assert len(repo.query_ledger("p1", limit=2)) == 2
        assert len(repo.query_ledger("p1", limit=None)) == 5


# ── T1.4 (W1 half) — the 3-row ASK chain shape ─────────────────────────


class TestAskChainShape:
    def test_three_row_ask_chain_is_representable(self, repo: POKRepository) -> None:
        """W1 half: the schema + repo ACCEPT the 3-row ASK chain shape.

        The gate NEVER writes; the TOOL executes (authority split). The
        chain is keyed by ``gate_id`` + ``phase``.
        """
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-1",
                phase="ASK_MINTED",
                verdict="ASK",
                reason="the caller claims an operational rule",
                judge_explanation="confidence 2/3 — needs the caller's call",
                confidence=0.66,
                candidate_slug="demo-01-x",
            )
        )
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-1",
                phase="ASK_ANSWERED",
                verdict="ACCEPT",
                answer_excerpt="yes, it breaks the deploy",
                answered_by="leader",
            )
        )
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-1",
                phase="ASK_EXECUTED",
                verdict="ACCEPT",
                executed=True,
                executed_by="worker",
                entry_id="row-1",
                candidate_slug="demo-01-x",
            )
        )
        chain = repo.ledger_gate_chain("p1", "g-1")
        assert len(chain) == 3
        assert [r.phase for r in chain] == [
            "ASK_MINTED",
            "ASK_ANSWERED",
            "ASK_EXECUTED",
        ]
        assert all(r.gate_id == "g-1" for r in chain)

    def test_plain_chain_keeps_two_rows(self, repo: POKRepository) -> None:
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1", gate_id="g-2", phase="ADMISSION", verdict="ACCEPT"
            )
        )
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-2",
                phase="EXECUTION",
                verdict="ACCEPT",
                executed=True,
                executed_by="worker",
            )
        )
        chain = repo.ledger_gate_chain("p1", "g-2")
        assert len(chain) == 2
        assert [r.phase for r in chain] == ["ADMISSION", "EXECUTION"]

    def test_the_ledger_alone_answers_why_accepted(
        self, repo: POKRepository
    ) -> None:
        """Success criterion #30 — no cross-store join is required."""
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-3",
                phase="ADMISSION",
                verdict="ACCEPT",
                reason="the deploy fails without this",
                judge_explanation="durable operational rule",
                confidence=0.9,
                candidate_slug="demo-01-x",
            )
        )
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-3",
                phase="EXECUTION",
                verdict="ACCEPT",
                executed=True,
                executed_by="worker",
                entry_id="row-3",
            )
        )
        chain = repo.ledger_gate_chain("p1", "g-3")
        verdict_row = next(r for r in chain if r.phase == "ADMISSION")
        execution_row = next(r for r in chain if r.phase == "EXECUTION")
        assert verdict_row.verdict == "ACCEPT"
        assert verdict_row.reason == "the deploy fails without this"
        assert verdict_row.judge_explanation == "durable operational rule"
        assert execution_row.executed is True
        assert execution_row.executed_by == "worker"
        assert execution_row.entry_id == "row-3"

    def test_a_merge_proposal_is_representable(self, repo: POKRepository) -> None:
        """§13.5 — the proposal fields can carry a merge round-trip."""
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-4",
                phase="ADMISSION",
                verdict="MERGE",
                target_slug="demo-01-existing",
                merge_kind="delta",
                proposed_body="the proposed merged body",
                proposed_body_sha256="a" * 64,
                target_body_sha256="b" * 64,
            )
        )
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-4",
                phase="EXECUTION",
                verdict="MERGE",
                merge_kind="delta",
                executed=True,
                executed_by="worker",
                executed_body_sha256="c" * 64,
            )
        )
        chain = repo.ledger_gate_chain("p1", "g-4")
        assert chain[0].merge_kind == "delta"
        assert chain[0].target_slug == "demo-01-existing"
        assert chain[0].proposed_body_sha256 == "a" * 64
        assert chain[1].executed_body_sha256 == "c" * 64
        # The proposal and the executed body are DIFFERENT anchors —
        # that is what makes the edit-distance measurable.
        assert chain[0].proposed_body_sha256 != chain[1].executed_body_sha256

    def test_a_no_delta_merge_records_no_write(self, repo: POKRepository) -> None:
        """F2: a no-delta merge is NO write — the audit says so."""
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-5",
                phase="ADMISSION",
                verdict="MERGE",
                target_slug="demo-01-existing",
                merge_kind="no_delta",
            )
        )
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                gate_id="g-5",
                phase="EXECUTION",
                verdict="MERGE",
                merge_kind="no_delta",
                executed=False,
                executed_by="worker",
            )
        )
        chain = repo.ledger_gate_chain("p1", "g-5")
        assert chain[1].executed is False
        assert chain[1].executed_body_sha256 is None
