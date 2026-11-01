"""Real-PostgreSQL integration pack for the project skill source (Phase 1).

Phase-1 task 11 acceptance — DB-backed index ⇒ real-PG required (the
index rows, embedding rows, and the A1 FK-wall proof are PG-visible
behavior; SQLite-only runs are explicitly out of scope for these legs):

* fresh scratch PG (the shared ``pg_engine`` fixture — allowlist-fenced,
  drop_all/create_all at session setup, drop at teardown);
* boot-migration verification: the ``.sql`` CREATE TABLE text matches
  the ``create_all``-built PG tables column-for-column (the two-artifact
  house rule; the OPERATIVE fresh-PG artifact is the model registration,
  per A10 — proven here by create_all building the tables);
* full index → invalidation → re-index cycle;
* ``embedded_at`` / ``last_error`` / ``valid`` state transitions;
* **A1 regression pin**: a ``pf-`` insert into the legacy
  ``skill_embeddings`` is PROVEN to hit the FK wall (documents WHY the
  dedicated no-FK table exists); the dedicated-table path is GREEN.

Run: ``pytest tests/postgres/test_project_skill_source_pg.py
--override-ini="addopts=" -m postgres`` (serial; xdist skip-all is
enforced by the shared conftest).
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

# Module-level imports so the lane's models (and the legacy skill models
# for the FK-wall pin) are registered in SQLModel.metadata BEFORE the
# session-scoped pg_engine fixture runs create_all (collection-time
# imports precede session fixtures).
import daemon.repositories.skill.models  # noqa: F401
import daemon.repositories.project_skill_file.models  # noqa: F401
from daemon.repositories.project_skill_file.models import (
    ProjectSkillEmbedding,
)
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillEmbeddingRepository,
    ProjectSkillFileRepository,
)
from daemon.repositories.skill.models import SkillEmbedding
from daemon.services.project_skill_source.model import mint_pseudo_id
from daemon.services.project_skill_source.service import ProjectSkillSourceService

from tests.helpers.project_skill_source_fixtures import (
    TEST_PROJECT_ID,
    FakeEmbeddingService,
    write_skill,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def pg_file_repo(pg_engine):
    return ProjectSkillFileRepository(pg_engine)


@pytest.fixture
def pg_embed_repo(pg_engine):
    return ProjectSkillEmbeddingRepository(pg_engine)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestBootMigrationArtifact:
    def test_sql_matches_models_column_for_column(self, pg_engine):
        """House two-artifact rule: for each lane table the ``.sql`` CREATE
        TABLE columns == the PG information_schema columns of the
        create_all-built table (name sets equal)."""
        lane_sqls = {
            "project_skill_files": None,
            "project_skill_embeddings": None,
            "project_skill_usage_records": None,
        }
        versions_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "daemon",
            "migrations",
            "versions",
        )
        for fname in os.listdir(versions_dir):
            for table in lane_sqls:
                if table in fname and fname.endswith(".sql"):
                    with open(os.path.join(versions_dir, fname), encoding="utf-8") as fh:
                        lane_sqls[table] = fh.read()
        assert all(lane_sqls.values()), "lane .sql files missing"

        with pg_engine.connect() as conn:
            for table, sql_text in lane_sqls.items():
                block = re.search(
                    rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\);",
                    sql_text,
                    re.S,
                )
                assert block, f"CREATE TABLE block not found for {table}"
                sql_cols = set()
                for line in block.group(1).splitlines():
                    line = line.strip().rstrip(",")
                    if not line or line.startswith("--"):
                        continue
                    m = re.match(r"([a-z_][a-z0-9_]*)\s+", line)
                    if m and m.group(1).upper() not in {
                        "PRIMARY",
                        "UNIQUE",
                        "FOREIGN",
                        "CHECK",
                        "CONSTRAINT",
                    }:
                        sql_cols.add(m.group(1))
                pg_cols = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t AND table_schema = 'public'"
                        ),
                        {"t": table},
                    )
                }
                assert pg_cols == sql_cols, (
                    f"{table}: .sql columns {sorted(sql_cols)} != PG columns {sorted(pg_cols)}"
                )

    def test_models_registered_pre_create_all(self, pg_engine):
        """A10: the operative fresh-PG artifact — the lane tables exist on
        the fresh scratch PG because the models were registered BEFORE
        create_all (proven by the tables being present at all)."""
        with pg_engine.connect() as conn:
            for table in (
                "project_skill_files",
                "project_skill_embeddings",
                "project_skill_usage_records",
            ):
                count = conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name = :t AND table_schema = 'public'"
                    ),
                    {"t": table},
                ).scalar()
                assert count == 1, f"{table} not created on fresh PG"


class TestIndexLifecyclePG:
    def test_full_index_invalidate_reindex_cycle(self, pg_engine, pg_file_repo, tmp_path):
        root = str(tmp_path)
        write_skill(root, "tester", "pg-alpha", body_text="PG alpha body.\n")
        write_skill(root, "tester", "pg-beta", body_text="PG beta body.\n")
        embed = FakeEmbeddingService()
        svc = ProjectSkillSourceService(
            file_repository=pg_file_repo,
            embedding_repository=pg_embed_repo,
            embedding_service=embed,
            llm_config={"model": "coding"},
            extra_roots=[],
            refresh_debounce_s=0.01,
        )

        async def cycle():
            s1 = await svc.ensure_index(root, TEST_PROJECT_ID)
            assert s1.skills_indexed == 2 and s1.full_scan is True
            rows = pg_file_repo.list_by_project(TEST_PROJECT_ID)
            assert {r.name for r in rows} == {"pg-alpha", "pg-beta"}
            # invalidation: body change picked up on next call
            body = tmp_path / ".agents" / "tester" / "skills" / "pg-alpha" / "pg-alpha.md"
            body.write_text("PG alpha body CHANGED.\n")
            s2 = await svc.ensure_index(root, TEST_PROJECT_ID)
            assert s2.changed == 1
            # re-index idempotency (NFR-IDEM-1) on real PG
            s3 = await svc.ensure_index(root, TEST_PROJECT_ID)
            assert s3.skills_indexed == 2
            assert len(pg_file_repo.list_by_project(TEST_PROJECT_ID)) == 2
            corpus = await svc.corpus_for(TEST_PROJECT_ID, root)
            assert any(s.content == "PG alpha body CHANGED.\n" for s in corpus)
            return rows

        rows = _run(cycle())
        assert all(r.body_sha256 for r in rows)

    def test_state_transitions_embedded_at_last_error_valid(
        self, pg_engine, pg_file_repo, pg_embed_repo, tmp_path
    ):
        root = str(tmp_path)
        write_skill(root, "tester", "trans-skill", body_text="Transitions body.\n")
        pseudo = mint_pseudo_id(os.path.realpath(root), TEST_PROJECT_ID, "tester", "trans-skill")

        # state 1: indexed, embeddings pending (embedded_at NULL, valid, no error)
        svc = ProjectSkillSourceService(
            file_repository=pg_file_repo,
            embedding_repository=pg_embed_repo,
            embedding_service=None,  # provider absent
            extra_roots=[],
        )

        async def index_only():
            return await svc.ensure_index(root, TEST_PROJECT_ID)

        _run(index_only())
        row = pg_file_repo.get(pseudo)
        assert row is not None and row.valid is True
        assert row.embedded_at is None and row.last_error is None

        # state 2: provider error → last_error set, still valid
        failing = FakeEmbeddingService(fail_mode="generate")
        svc2 = ProjectSkillSourceService(
            file_repository=pg_file_repo,
            embedding_repository=pg_embed_repo,
            embedding_service=failing,
            extra_roots=[],
            refresh_debounce_s=0.01,
        )

        async def refresh_fail():
            await svc2.ensure_index(root, TEST_PROJECT_ID)
            await asyncio.sleep(0.05)
            await asyncio.gather(*svc2._refresh_tasks)

        _run(refresh_fail())
        row = pg_file_repo.get(pseudo)
        assert row.valid is True
        assert row.embedded_at is None
        assert "trigger_generation_failed" in (row.last_error or "")

        # state 3: successful refresh → embedded_at stamped, error cleared
        good = FakeEmbeddingService(queries=3)
        svc3 = ProjectSkillSourceService(
            file_repository=pg_file_repo,
            embedding_repository=pg_embed_repo,
            embedding_service=good,
            extra_roots=[],
            refresh_debounce_s=0.01,
        )

        async def refresh_ok():
            await svc3.ensure_index(root, TEST_PROJECT_ID)
            await asyncio.sleep(0.05)
            await asyncio.gather(*svc3._refresh_tasks)

        _run(refresh_ok())
        row = pg_file_repo.get(pseudo)
        assert row.embedded_at is not None
        assert row.last_error is None
        assert pg_embed_repo.count_by_skill(pseudo) == 3

        # state 4: vanish → row deleted WITH its embedding rows (A4-iv)
        import shutil

        shutil.rmtree(
            os.path.join(root, ".agents", "tester", "skills", "trans-skill")
        )

        async def reindex_after_vanish():
            return await svc3.ensure_index(root, TEST_PROJECT_ID)

        stats = _run(reindex_after_vanish())
        assert stats.vanished == 1
        assert pg_file_repo.get(pseudo) is None
        assert pg_embed_repo.count_by_skill(pseudo) == 0

    def test_invalid_skill_indexed_as_listed(self, pg_engine, pg_file_repo, tmp_path):
        root = str(tmp_path)
        write_skill(root, "tester", "broken-skill", body_text="")
        svc = ProjectSkillSourceService(
            file_repository=pg_file_repo, embedding_repository=None, extra_roots=[]
        )

        async def scan():
            return await svc.ensure_index(root, TEST_PROJECT_ID)

        stats = _run(scan())
        assert stats.skills_indexed == 0 and stats.invalid_skills == 1
        rows = pg_file_repo.list_by_project(TEST_PROJECT_ID)
        assert len(rows) == 1 and rows[0].valid is False
        assert "body_empty" in (rows[0].last_error or "")


class TestA1FkWallPin:
    def test_pf_insert_into_legacy_skill_embeddings_rejected(self, pg_engine):
        """THE A1 REGRESSION PIN (documents WHY the dedicated table
        exists): ``skill_embeddings.skill_id`` is
        ForeignKey("skills.id", ondelete="CASCADE") — a ``pf-`` insert is
        REJECTED on real PG with a ForeignKeyViolation. RED-documented:
        the insert fails; the lane's own table accepts the same row."""
        pseudo = "pf-" + "a" * 16
        # RED leg: the legacy FK'd table refuses the pf- id
        with pytest.raises(Exception) as excinfo:
            with Session(pg_engine) as session:
                session.add(
                    SkillEmbedding(
                        skill_id=pseudo,
                        trigger_query="trigger",
                        embedding=[0.1, 0.2],
                    )
                )
                session.commit()
        assert "ForeignKeyViolation" in type(excinfo.value).__name__ or (
            "foreign key" in str(excinfo.value).lower()
        ), f"expected the FK wall, got: {excinfo.value!r}"

    def test_dedicated_table_path_green(self, pg_engine, pg_embed_repo):
        """GREEN leg: the SAME pf- id inserts cleanly into the dedicated
        no-FK table (A1's whole point)."""
        pseudo = "pf-" + "a" * 16
        written = pg_embed_repo.insert_many(
            pseudo, [("trigger one", [0.1, 0.2]), ("trigger two", [0.3, 0.4])]
        )
        assert written == 2
        rows = pg_embed_repo.list_by_skill(pseudo)
        assert {r.trigger_query for r in rows} == {"trigger one", "trigger two"}
        # and replace-for-skill (A4-vi) works on real PG
        written2, removed = pg_embed_repo.replace_for_skill(
            pseudo, [("trigger three", [0.5, 0.6])]
        )
        assert (written2, removed) == (1, 2)
        assert pg_embed_repo.count_by_skill(pseudo) == 1

    def test_gc_sweep_on_real_pg(self, pg_engine, pg_embed_repo, pg_file_repo):
        """A4(v) gc on real PG: orphan embedding rows (skill_id ∉
        project_skill_files) are removed."""
        orphan = "pf-" + "b" * 16
        pg_embed_repo.insert_many(orphan, [("orphan q", [1.0])])
        bound = "pf-" + "c" * 16
        pg_embed_repo.insert_many(bound, [("bound q", [1.0])])
        from daemon.repositories.project_skill_file.models import ProjectSkillFile

        with Session(pg_engine) as session:
            session.add(
                ProjectSkillFile(
                    id=bound,
                    project_id=TEST_PROJECT_ID,
                    root_path="/root",
                    namespace="ns",
                    name="n",
                    address="pf:ns:n",
                    description="",
                    body_path="n.md",
                    manifest_sha256="m",
                    body_sha256="b",
                    indexed_at="2026-10-31T00:00:00+00:00",
                )
            )
            session.commit()
        svc = ProjectSkillSourceService(embedding_repository=pg_embed_repo, extra_roots=[])
        removed = _run(svc.gc_sweep())
        assert removed >= 1
        assert pg_embed_repo.count_by_skill(orphan) == 0
        assert pg_embed_repo.count_by_skill(bound) == 1


def _skill_tables_from_live_pg(engine) -> list[str]:
    """FR-P1-5 hardening (PG leg): enumerate the skill tables from LIVE
    ``information_schema`` introspection — never a hardcoded list."""
    with engine.connect() as conn:
        names = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name LIKE '%skill%'"
                )
            )
        ]
    return sorted(names)


def _grep_skill_tables_pg(engine, needle_windows: list[str]) -> list[str]:
    """Grep every column value (stringified) of every live skill table."""
    violations: list[str] = []
    with engine.connect() as conn:
        for table in _skill_tables_from_live_pg(engine):
            rows = conn.execute(text(f'SELECT * FROM "{table}"')).fetchall()
            for row in rows:
                for idx, value in enumerate(row):
                    blob = value if isinstance(value, str) else str(value)
                    for window in needle_windows:
                        if window and window in blob:
                            violations.append(f"{table}[col{idx}] contains {window!r}")
    return violations


class TestP1SentinelPG:
    def test_body_sentinel_never_persisted_on_real_pg(
        self, pg_engine, pg_file_repo, pg_embed_repo, tmp_path
    ):
        """FR-P1-5 on scratch PG (Phase-1 exit criterion): a HIGH-ENTROPY
        body sentinel survives index → corpus → explicit-load with ZERO
        verbatim appearances in ANY live skill table (12-char windows;
        table list from ``information_schema``)."""
        sentinel = "c4a9e13f7b2d48a6915e30cd2fb7a864"
        body = f"# PG sentinel skill\n\nUse when {sentinel} appears in a task.\n"
        root = str(tmp_path)
        write_skill(root, "tester", "pg-sentinel", body_text=body)
        svc = ProjectSkillSourceService(
            file_repository=pg_file_repo,
            embedding_repository=pg_embed_repo,
            embedding_service=FakeEmbeddingService(),
            llm_config={"model": "coding"},
            extra_roots=[],
            refresh_debounce_s=0.01,
        )

        async def cycle():
            await svc.ensure_index(root, TEST_PROJECT_ID)
            corpus = await svc.corpus_for(TEST_PROJECT_ID, root)
            assert any(sentinel in skill.content for skill in corpus)
            hit = await svc.lookup_explicit("pf:tester:pg-sentinel", TEST_PROJECT_ID)
            assert hit is not None and sentinel in hit.content
            await asyncio.sleep(0.05)
            await asyncio.gather(*svc._refresh_tasks)

        _run(cycle())

        # non-vacuity: the lane tables were introspected AND carry rows
        tables = _skill_tables_from_live_pg(pg_engine)
        for expected in (
            "skills",
            "skill_embeddings",
            "project_skill_files",
            "project_skill_embeddings",
        ):
            assert expected in tables, f"{expected} missing from live PG introspection"
        assert pg_file_repo.list_by_project(TEST_PROJECT_ID), "no index rows written"
        pseudo_id = next(
            row.id
            for row in pg_file_repo.list_by_project(TEST_PROJECT_ID)
            if row.name == "pg-sentinel"
        )
        assert pg_embed_repo.count_by_skill(pseudo_id) > 0, "no embedding rows written"

        windows = [sentinel[i : i + 12] for i in range(0, len(sentinel) - 11)]
        violations = _grep_skill_tables_pg(pg_engine, windows)
        assert violations == [], f"P1 violated on PG: {violations}"


class TestA3TwoRootsOneProjectPG:
    def test_distinct_rows_and_ids_on_real_pg(self, pg_engine, pg_file_repo, tmp_path):
        """A3 on real PG: two roots bound to one project keep DISTINCT
        index rows and pseudo-ids (the unique key carries root_path)."""
        root_a = tmp_path / "root-a"
        root_b = tmp_path / "root-b"
        for root in (root_a, root_b):
            (root / ".agents" / "tester" / "skills").mkdir(parents=True)
        write_skill(str(root_a), "tester", "same-name", body_text="ROOT A BODY\n")
        write_skill(str(root_b), "tester", "same-name", body_text="ROOT B BODY\n")
        probe_project = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        svc = ProjectSkillSourceService(
            file_repository=pg_file_repo, embedding_repository=None, extra_roots=[]
        )

        async def scan():
            await svc.ensure_index(str(root_a), probe_project)
            await svc.ensure_index(str(root_b), probe_project)

        _run(scan())
        rows = pg_file_repo.list_by_project(probe_project)
        assert len(rows) == 2  # unique key includes root_path
        assert len({r.id for r in rows}) == 2
        assert len({r.root_path for r in rows}) == 2
        # both are distinct pseudo-ids for the same (ns, name)
        for row in rows:
            other_root = str(root_b) if row.root_path == os.path.realpath(str(root_a)) else str(root_a)
            assert row.id != mint_pseudo_id(
                os.path.realpath(other_root), probe_project, "tester", "same-name"
            )


class TestRepositoryCrudRoundTripPG:
    """Task-3 acceptance: CRUD round-trip on scratch PG (file + companion
    usage repositories — the primitives Phase 3 consumes)."""

    def test_file_repository_round_trip(self, pg_engine, pg_file_repo):
        from daemon.repositories.project_skill_file.models import ProjectSkillFile

        project = "cccccccc-dddd-eeee-ffff-000000000000"
        pseudo = mint_pseudo_id("/crud-root", project, "tester", "crud-skill")
        row = ProjectSkillFile(
            id=pseudo,
            project_id=project,
            root_path="/crud-root",
            namespace="tester",
            name="crud-skill",
            address="pf:tester:crud-skill",
            description="first description",
            body_path="crud-skill.md",
            manifest_sha256="m1",
            body_sha256="b1",
            indexed_at="2026-10-31T00:00:00+00:00",
        )
        pg_file_repo.upsert(row)
        got = pg_file_repo.get(pseudo)
        assert got is not None and got.description == "first description"
        assert len(pg_file_repo.list_by_project(project)) == 1
        assert len(pg_file_repo.list_by_root("/crud-root")) == 1

        # update-in-place on the natural PK
        row.description = "second description"
        row.body_sha256 = "b2"
        pg_file_repo.upsert(row)
        got = pg_file_repo.get(pseudo)
        assert got.description == "second description" and got.body_sha256 == "b2"
        assert len(pg_file_repo.list_by_project(project)) == 1  # no dupe

        # embedding-state toggle
        assert pg_file_repo.set_embedding_state(pseudo, "2026-10-31T01:00:00+00:00", None) is True
        assert pg_file_repo.get(pseudo).embedded_at == "2026-10-31T01:00:00+00:00"

        # delete-with-row semantics + idempotent second delete
        assert pg_file_repo.delete_skill(pseudo) is True
        assert pg_file_repo.get(pseudo) is None
        assert pg_file_repo.delete_skill(pseudo) is False

    def test_usage_repository_round_trip(self, pg_engine):
        from daemon.repositories.project_skill_file.models import ProjectSkillUsageRecord
        from daemon.repositories.project_skill_file.repository import (
            ProjectSkillUsageRecordRepository,
        )

        repo = ProjectSkillUsageRecordRepository(pg_engine)
        project = "dddddddd-eeee-ffff-0000-111111111111"
        pseudo = mint_pseudo_id("/crud-root", project, "tester", "usage-skill")
        record = ProjectSkillUsageRecord(
            skill_id=pseudo,
            project_id=project,
            instance_id="inst-usage",
            agent_id="tester",
            task_message="do the thing",
        )
        inserted = repo.insert(record)
        assert inserted.id

        latest = repo.get_latest_for_skill_instance(pseudo, "inst-usage")
        assert latest is not None and latest.id == inserted.id

        updated = repo.update_completion(
            inserted.id, task_succeeded=True, iterations=2, duration_seconds=7
        )
        assert updated is not None and updated.task_succeeded is True
        assert updated.iterations == 2 and updated.duration_seconds == 7

        feedback = repo.update_feedback(inserted.id, True, "worked well")
        assert feedback is not None and feedback.feedback_applied is True
        assert feedback.feedback_note == "worked well"

        assert len(repo.list_by_skill(pseudo)) == 1
        assert repo.delete_by_skill(pseudo) == 1
        assert repo.list_by_skill(pseudo) == []
