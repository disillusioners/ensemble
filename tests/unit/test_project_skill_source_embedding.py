"""Unit pack: embedding refresh dispatch (Q3 + A1 + S1) + the P1 sentinel
(FR-P1-5, W-7 spec).

Phase-1 task 8 acceptance: rows written to ``project_skill_embeddings``
ONLY (zero rows appear in the legacy ``skill_embeddings`` under pf- ids);
async refresh proven non-blocking; provider-absent path returns corpus
with embeddings_pending > 0 + last_error; delete-after-success proven.
P1 sentinel: a HIGH-ENTROPY body token grepped to ZERO rows across ALL
skill tables enumerated from live SQLModel metadata (never hardcoded).
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlmodel import SQLModel, select

from daemon.repositories.project_skill_file.repository import (
    ProjectSkillEmbeddingRepository,
    ProjectSkillFileRepository,
)

from tests.helpers.project_skill_source_fixtures import (
    TEST_PROJECT_ID,
    FakeEmbeddingService,
    canon_root,
    make_service,
    sqlite_engine,
    write_skill,
)


def _skill_tables_from_metadata() -> list[str]:
    """FR-P1-5 hardening: the table list is enumerated from LIVE
    SQLModel metadata — never hardcoded (the vacuous-pinhole risk)."""
    skill_like = [
        name
        for name in SQLModel.metadata.tables
        if "skill" in name  # skills + every skill_* table incl. the lane's three
    ]
    assert "skills" in skill_like
    assert "skill_embeddings" in skill_like
    assert "project_skill_files" in skill_like
    assert "project_skill_embeddings" in skill_like
    assert "skill_bank" in skill_like
    assert "skill_usage_records" in skill_like
    return sorted(skill_like)


def _grep_all_skill_tables(engine, needle_windows: list[str]) -> list[str]:
    """Grep every TEXT-convertible column of every skill table for any
    verbatim needle window. Returns violation descriptions."""
    from sqlalchemy import text

    violations: list[str] = []
    with engine.connect() as conn:
        for table in _skill_tables_from_metadata():
            try:
                rows = conn.execute(text(f'SELECT * FROM "{table}"')).fetchall()
            except Exception:
                continue  # table absent in this engine — nothing to grep
            for row in rows:
                for idx, value in enumerate(row):
                    if not isinstance(value, str):
                        continue
                    for window in needle_windows:
                        if window and window in value:
                            violations.append(f"{table}[col{idx}] contains {window!r}")
    return violations


class TestEmbeddingDispatch:
    @pytest.mark.asyncio
    async def test_rows_written_to_dedicated_table_only(self, canon_root, sqlite_engine):
        """A1: the refresh writes ``project_skill_embeddings`` — and ZERO
        rows appear in the legacy ``skill_embeddings`` under pf- ids."""
        import json

        from daemon.repositories.skill.models import SkillEmbedding
        from sqlmodel import Session

        embed = FakeEmbeddingService()
        svc = make_service(
            sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01
        )
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        await asyncio.sleep(0.05)
        await asyncio.gather(*svc._refresh_tasks)
        pse = ProjectSkillEmbeddingRepository(sqlite_engine)
        index = svc._roots[os.path.realpath(canon_root)]
        total = 0
        for meta in index.metas.values():
            total += pse.count_by_skill(meta.pseudo_id)
        assert total == 3 * 3  # 3 skills × 3 queries
        # zero pf- rows in the legacy table
        with Session(sqlite_engine) as session:
            legacy_rows = list(session.exec(select(SkillEmbedding)))
        assert legacy_rows == []

    @pytest.mark.asyncio
    async def test_provider_absent_is_graceful(self, canon_root, sqlite_engine):
        """No embedding service ⇒ BM25-only; embeddings_pending > 0; the
        corpus is complete; nothing crashes."""
        svc = make_service(sqlite_engine=sqlite_engine, embedding_service=None)
        stats = await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        assert stats.skills_indexed == 3
        corpus = await svc.corpus_for(TEST_PROJECT_ID, canon_root)
        assert len(corpus) == 3
        status = svc.snapshot_status()
        assert status["embeddings_pending"] == 3

    @pytest.mark.asyncio
    async def test_provider_error_sets_last_error_never_fails_corpus(
        self, canon_root, sqlite_engine
    ):
        embed = FakeEmbeddingService(fail_mode="generate")
        svc = make_service(
            sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01
        )
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        await asyncio.sleep(0.05)
        await asyncio.gather(*svc._refresh_tasks)
        repo = ProjectSkillFileRepository(sqlite_engine)
        index = svc._roots[os.path.realpath(canon_root)]
        for meta in index.metas.values():
            row = repo.get(meta.pseudo_id)
            assert row.last_error and "trigger_generation_failed" in row.last_error
            assert row.valid is True  # the skill stays in the corpus
        corpus = await svc.corpus_for(TEST_PROJECT_ID, canon_root)
        assert len(corpus) == 3

    @pytest.mark.asyncio
    async def test_refresh_is_fire_and_forget(self, canon_root, sqlite_engine):
        """The corpus/callers never await the refresh (risk 12: a git
        pull must not add latency to a turn)."""
        embed = FakeEmbeddingService()
        svc = make_service(
            sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.5
        )
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        assert embed.generate_calls == []  # debounce window has not elapsed
        # the refresh is already scheduled as a background task
        assert len(svc._refresh_tasks) == 3
        corpus = await svc.corpus_for(TEST_PROJECT_ID, canon_root)
        assert len(corpus) == 3  # corpus returned without awaiting refreshes
        await asyncio.gather(*svc._refresh_tasks)
        assert len(embed.generate_calls) == 3

    @pytest.mark.asyncio
    async def test_generation_view_carries_body(self, tmp_path, sqlite_engine):
        """S1/Q3: generation grounds on name+description+BODY (the DB-side
        pattern) — the fake records the content it saw."""
        token = "a" * 32
        write_skill(tmp_path, "tester", "gen-skill", body_text=f"Body with {token} inside.\n")
        embed = FakeEmbeddingService()
        svc = make_service(
            sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01
        )
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        await asyncio.sleep(0.05)
        await asyncio.gather(*svc._refresh_tasks)
        assert embed.generation_bodies == [f"Body with {token} inside.\n"]


class TestP1Sentinel:
    @pytest.mark.asyncio
    async def test_body_sentinel_never_persisted(self, tmp_path, sqlite_engine):
        """FR-P1-5 (W-7 spec): a HIGH-ENTROPY 32-hex token planted in the
        body survives an index + corpus + explicit-load cycle with ZERO
        verbatim appearances in ANY skill table (windows ≥8 chars). The
        table list comes from live metadata introspection."""
        sentinel = "9f2c7a1e5b8d403fa6c2e19b73d8045c"
        body = (
            "# Demo skill\n\nUse this skill when the sentinel "
            f"{sentinel} appears in a task.\n"
        )
        write_skill(tmp_path, "tester", "sentinel-skill", body_text=body)
        embed = FakeEmbeddingService()
        svc = make_service(
            sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01
        )
        # the full Phase-1 cycle: index → corpus (search-side primitive)
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        corpus = await svc.corpus_for(TEST_PROJECT_ID, str(tmp_path))
        assert any(sentinel in s.content for s in corpus)  # canon serves it
        # … → explicit load (load-side primitive)
        hit = await svc.lookup_explicit("sentinel-skill", TEST_PROJECT_ID)
        assert hit is not None and sentinel in hit.content

        windows = [sentinel[i : i + 12] for i in range(0, len(sentinel) - 11)]
        violations = _grep_all_skill_tables(sqlite_engine, windows)
        assert violations == [], f"P1 violated: {violations}"

    @pytest.mark.asyncio
    async def test_trigger_queries_may_paraphrase_but_not_verbatim(
        self, tmp_path, sqlite_engine
    ):
        """W-7: derived trigger queries are PERMITTED to paraphrase body
        content semantically; only VERBATIM body bytes are forbidden."""
        sentinel = "ff11ee22dd33cc44bb55aa6677889900"
        body = f"Instructions mentioning {sentinel} verbatim once.\n"
        write_skill(tmp_path, "tester", "para-skill", body_text=body)
        # a "derivative" embedding service whose queries mention the topic
        class Derivative(FakeEmbeddingService):
            async def generate_trigger_queries(self, skill):
                # paraphrase: topic words from the body, NO verbatim token
                return ["instructions mentioning tokens verbatim", "para skill usage"]

        svc = make_service(
            sqlite_engine=sqlite_engine,
            embedding_service=Derivative(),
            refresh_debounce_s=0.01,
        )
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        await asyncio.sleep(0.05)
        await asyncio.gather(*svc._refresh_tasks)
        pse = ProjectSkillEmbeddingRepository(sqlite_engine)
        index = svc._roots[os.path.realpath(str(tmp_path))]
        pseudo = next(iter(index.metas.keys()))
        assert pse.count_by_skill(pseudo) == 2  # derived rows exist
        windows = [sentinel[i : i + 12] for i in range(0, len(sentinel) - 11)]
        violations = _grep_all_skill_tables(sqlite_engine, windows)
        assert violations == [], f"P1 violated: {violations}"
