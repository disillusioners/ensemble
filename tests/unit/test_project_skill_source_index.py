"""Unit pack: index idempotency, concurrency coalescing, caps, lifecycle
(A4 rules i-vii), git-pull pickup, vanished dirs, 0-re-scan invariant.

Phase-1 tasks 6-7 acceptance + NFR-IDEM-1/2 + NFR-PERF-4.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import time

import pytest

from daemon.repositories.project_skill_file.repository import (
    ProjectSkillEmbeddingRepository,
    ProjectSkillFileRepository,
)
from daemon.services.project_skill_source.manifest import (
    MANIFEST_MAX_BYTES,
    REASON_BODY_TOO_LARGE,
    REASON_NAME_MISMATCH,
)
from daemon.services.project_skill_source.model import mint_pseudo_id

from tests.helpers.project_skill_source_fixtures import (
    TEST_PROJECT_ID,
    VALID_MANIFEST,
    FakeEmbeddingService,
    canon_root,
    make_service,
    sqlite_engine,
    write_skill,
)


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_reindex_x2_identical_rows(self, canon_root, sqlite_engine):
        """NFR-IDEM-1: re-indexing the same project produces the SAME
        index (no dupes, no drift)."""
        svc = make_service(sqlite_engine=sqlite_engine)
        s1 = await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        s2 = await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        assert s1.skills_indexed == s2.skills_indexed == 3
        repo = ProjectSkillFileRepository(sqlite_engine)
        rows = repo.list_by_project(TEST_PROJECT_ID)
        assert len(rows) == 3
        ids = sorted(r.id for r in rows)
        s3 = await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        assert sorted(r.id for r in repo.list_by_project(TEST_PROJECT_ID)) == ids
        assert s3.skills_indexed == 3

    @pytest.mark.asyncio
    async def test_second_pass_is_stat_gate_not_full_scan(self, canon_root, sqlite_engine):
        svc = make_service(sqlite_engine=sqlite_engine)
        first = await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        assert first.full_scan is True
        second = await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        assert second.full_scan is False  # stat-gate pass, not a re-scan


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_first_touch_coalesces(self, canon_root, sqlite_engine):
        """NFR-IDEM-2: concurrent first-touches coalesce — one build,
        all callers await it, no duplicate rows."""
        svc = make_service(sqlite_engine=sqlite_engine)
        results = await asyncio.gather(
            *(svc.ensure_index(canon_root, TEST_PROJECT_ID) for _ in range(6))
        )
        # exactly ONE full scan happened (the rest either awaited the same
        # lock and saw the built index, or stat-gated cleanly)
        assert sum(1 for r in results if r.full_scan) <= 1
        assert all(r.skills_indexed == 3 for r in results)
        repo = ProjectSkillFileRepository(sqlite_engine)
        assert len(repo.list_by_project(TEST_PROJECT_ID)) == 3  # no dupes

    @pytest.mark.asyncio
    async def test_concurrent_corpus_reads_consistent(self, canon_root, sqlite_engine):
        svc = make_service(sqlite_engine=sqlite_engine)
        corpora = await asyncio.gather(
            *(svc.corpus_for(TEST_PROJECT_ID, canon_root) for _ in range(5))
        )
        for corpus in corpora:
            assert sorted(s.name for s in corpus) == ["alpha-skill", "beta-skill", "gamma-skill"]


class TestCaps:
    @pytest.mark.asyncio
    async def test_skill_dir_cap_truncates(self, tmp_path, sqlite_engine):
        for i in range(130):
            write_skill(tmp_path, "tester", f"cap-skill-{i:03d}")
        svc = make_service(sqlite_engine=sqlite_engine)
        stats = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert stats.skills_indexed == 128
        assert any("cap hit" in note for note in stats.skip_notes)

    @pytest.mark.asyncio
    async def test_manifest_cap_lists_invalid(self, tmp_path, sqlite_engine):
        write_skill(
            tmp_path,
            "tester",
            "big-manifest",
            manifest={**VALID_MANIFEST, "description": "x" * (MANIFEST_MAX_BYTES + 5)},
        )
        svc = make_service(sqlite_engine=sqlite_engine)
        stats = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert stats.skills_indexed == 0
        assert stats.invalid_skills == 1
        # indexed-as-listed: the invalid entry is visible in the DB
        repo = ProjectSkillFileRepository(sqlite_engine)
        rows = repo.list_by_project(TEST_PROJECT_ID)
        assert len(rows) == 1 and rows[0].valid is False
        assert "manifest_too_large" in (rows[0].last_error or "")

    @pytest.mark.asyncio
    async def test_body_cap_excluded_from_corpus(self, tmp_path, sqlite_engine):
        """D-Q2: an oversized body is indexed-as-listed and EXCLUDED from
        the search corpus."""
        write_skill(tmp_path, "tester", "chonky", body_text="a" * (64 * 1024 + 1))
        write_skill(tmp_path, "tester", "normal")
        svc = make_service(sqlite_engine=sqlite_engine)
        stats = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert stats.skills_indexed == 1
        corpus = await svc.corpus_for(TEST_PROJECT_ID, str(tmp_path))
        assert [s.name for s in corpus] == ["normal"]
        rows = ProjectSkillFileRepository(sqlite_engine).list_by_project(TEST_PROJECT_ID)
        chonky = [r for r in rows if r.name == "chonky"]
        assert len(chonky) == 1 and chonky[0].valid is False
        assert REASON_BODY_TOO_LARGE in (chonky[0].last_error or "")


class TestOneBadSkillDoesNotPoison:
    @pytest.mark.asyncio
    async def test_scan_survives_bad_skills(self, tmp_path, sqlite_engine):
        write_skill(tmp_path, "tester", "good")
        write_skill(tmp_path, "tester", "mismatch", manifest={**VALID_MANIFEST, "name": "other"})
        write_skill(tmp_path, "tester", "nojson", manifest_text="{oops")
        svc = make_service(sqlite_engine=sqlite_engine)
        stats = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert stats.skills_indexed == 1
        assert stats.invalid_skills == 2
        corpus = await svc.corpus_for(TEST_PROJECT_ID, str(tmp_path))
        assert [s.name for s in corpus] == ["good"]
        reasons = {e["reason"] for e in svc._roots[os.path.realpath(str(tmp_path))].invalid}
        assert reasons == {REASON_NAME_MISMATCH, "invalid_json"}

    @pytest.mark.asyncio
    async def test_fixed_skill_recovers_on_next_gate(self, tmp_path, sqlite_engine):
        write_skill(tmp_path, "tester", "maybe", manifest={**VALID_MANIFEST, "name": "other"})
        svc = make_service(sqlite_engine=sqlite_engine, refresh_debounce_s=0.01)
        stats = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert stats.invalid_skills == 1 and stats.skills_indexed == 0
        # fix the manifest (name == dir) — mtime changes
        manifest_path = (
            tmp_path / ".agents" / "tester" / "skills" / "maybe" / "skill.json"
        )
        manifest = json.loads(manifest_path.read_text())
        manifest["name"] = "maybe"
        manifest_path.write_text(json.dumps(manifest))
        stats2 = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert stats2.skills_indexed == 1 and stats2.invalid_skills == 0
        repo = ProjectSkillFileRepository(sqlite_engine)
        rows = repo.list_by_project(TEST_PROJECT_ID)
        assert len(rows) == 1 and rows[0].valid is True


class TestA4Lifecycle:
    @pytest.mark.asyncio
    async def test_rule_i_wholesale_swap_readers_see_consistent_state(
        self, canon_root, sqlite_engine
    ):
        """A4(i): readers see pre- or post-state, never torn — the corpus
        list captured before a change stays intact while the index moves."""
        svc = make_service(sqlite_engine=sqlite_engine, refresh_debounce_s=0.01)
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        pre_state = await svc.corpus_for(TEST_PROJECT_ID, canon_root)
        # mutate: rewrite alpha's body + add a new skill
        alpha_body = (
            os.path.join(canon_root, ".agents", "tester", "skills", "alpha-skill")
        )
        with open(os.path.join(alpha_body, "alpha-skill.md"), "w") as fh:
            fh.write("Alpha body CHANGED.\n")
        write_skill(canon_root, "tester", "delta-skill", body_text="Delta.\n")
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        # the PRE swap list is untouched (wholesale swap, no in-place tear)
        assert sorted(s.name for s in pre_state) == ["alpha-skill", "beta-skill", "gamma-skill"]
        assert all(s.content == "Alpha body.\n" for s in pre_state if s.name == "alpha-skill")
        post = await svc.corpus_for(TEST_PROJECT_ID, canon_root)
        assert sorted(s.name for s in post) == [
            "alpha-skill", "beta-skill", "delta-skill", "gamma-skill"
        ]
        assert any(s.content == "Alpha body CHANGED.\n" for s in post)

    @pytest.mark.asyncio
    async def test_rule_ii_db_failure_never_fails_corpus(self, canon_root, sqlite_engine):
        svc = make_service(sqlite_engine=sqlite_engine)
        # break the repo AFTER construction
        class Boom:
            def sync_scan(self, *a, **k):
                raise RuntimeError("db down")

        svc._files = Boom()
        corpus = await svc.corpus_for(TEST_PROJECT_ID, canon_root)
        assert len(corpus) == 3  # in-memory snapshot authoritative (A4-ii)
        index = svc._roots[os.path.realpath(canon_root)]
        assert index.stats.last_error and "db_sync_failed" in index.stats.last_error

    @pytest.mark.asyncio
    async def test_rule_iii_vector_from_same_root(self, tmp_path, sqlite_engine):
        """A3/A4(iii): two roots, one project → distinct pseudo-ids and
        the generation view carries the SAME root's body."""
        root_a = tmp_path / "root-a"
        root_b = tmp_path / "root-b"
        for root in (root_a, root_b):
            (root / ".agents" / "tester" / "skills").mkdir(parents=True)
        write_skill(str(root_a), "tester", "shared-name", body_text="ROOT A BODY")
        write_skill(str(root_b), "tester", "shared-name", body_text="ROOT B BODY")
        embed = FakeEmbeddingService()
        svc = make_service(sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01)
        await svc.ensure_index(str(root_a), TEST_PROJECT_ID)
        await svc.ensure_index(str(root_b), TEST_PROJECT_ID)
        from daemon.services.project_skill_source.model import mint_pseudo_id

        id_a = mint_pseudo_id(os.path.realpath(str(root_a)), TEST_PROJECT_ID, "tester", "shared-name")
        id_b = mint_pseudo_id(os.path.realpath(str(root_b)), TEST_PROJECT_ID, "tester", "shared-name")
        assert id_a != id_b
        # let the debounced refreshes run
        await asyncio.sleep(0.2)
        await asyncio.gather(*svc._refresh_tasks)
        # the generation views saw DIFFERENT bodies (same-root pairing)
        bodies = sorted(set(embed.generation_bodies))
        assert "ROOT A BODY" in bodies and "ROOT B BODY" in bodies

    @pytest.mark.asyncio
    async def test_rule_iv_delete_with_row(self, canon_root, sqlite_engine):
        from daemon.repositories.project_skill_file.repository import (
            ProjectSkillEmbeddingRepository,
        )

        embed_repo = ProjectSkillEmbeddingRepository(sqlite_engine)
        embed = FakeEmbeddingService()
        svc = make_service(sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01)
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        await asyncio.sleep(0.1)
        await asyncio.gather(*svc._refresh_tasks)
        index = svc._roots[os.path.realpath(canon_root)]
        pseudo_id = next(iter(index.metas.keys()))
        assert embed_repo.count_by_skill(pseudo_id) > 0
        # delete the index row → embedding rows die WITH it
        assert ProjectSkillFileRepository(sqlite_engine).delete_skill(pseudo_id) is True
        assert embed_repo.count_by_skill(pseudo_id) == 0

    @pytest.mark.asyncio
    async def test_rule_v_gc_sweep_removes_orphans(self, canon_root, sqlite_engine):
        embed_repo = ProjectSkillEmbeddingRepository(sqlite_engine)
        orphan_id = "pf-" + "f" * 16
        embed_repo.insert_many(orphan_id, [("q", [1.0, 2.0])])
        assert embed_repo.count_by_skill(orphan_id) == 1
        svc = make_service(sqlite_engine=sqlite_engine)
        removed = await svc.gc_sweep()
        assert removed >= 1
        assert embed_repo.count_by_skill(orphan_id) == 0

    @pytest.mark.asyncio
    async def test_rule_vi_delete_after_success(self, tmp_path, sqlite_engine):
        """A4(vi): a FAILED generation leaves the old rows untouched; a
        successful one replaces them (stale beats permanently-absent)."""
        from daemon.repositories.project_skill_file.repository import (
            ProjectSkillEmbeddingRepository,
        )

        root = str(tmp_path)
        write_skill(tmp_path, "tester", "vec-skill")
        embed_repo = ProjectSkillEmbeddingRepository(sqlite_engine)
        # seed "old" rows as if from a prior generation
        pseudo = mint_pseudo_id(os.path.realpath(root), TEST_PROJECT_ID, "tester", "vec-skill")
        embed_repo.insert_many(pseudo, [("old query", [9.0, 9.0])])

        failing = FakeEmbeddingService(fail_mode="generate")
        svc = make_service(sqlite_engine=sqlite_engine, embedding_service=failing, refresh_debounce_s=0.01)
        await svc.ensure_index(root, TEST_PROJECT_ID)
        await asyncio.sleep(0.1)
        await asyncio.gather(*svc._refresh_tasks)
        # failed generation → old rows SURVIVE (never deleted-then-lost)
        rows = embed_repo.list_by_skill(pseudo)
        assert len(rows) == 1 and rows[0].trigger_query == "old query"
        repo = ProjectSkillFileRepository(sqlite_engine)
        assert "trigger_generation_failed" in (repo.get(pseudo).last_error or "")

        # now a successful refresh REPLACES them (delete-after-success)
        good = FakeEmbeddingService(queries=3)
        svc2 = make_service(sqlite_engine=sqlite_engine, embedding_service=good, refresh_debounce_s=0.01)
        await svc2.ensure_index(root, TEST_PROJECT_ID)
        await asyncio.sleep(0.1)
        await asyncio.gather(*svc2._refresh_tasks)
        rows = embed_repo.list_by_skill(pseudo)
        assert len(rows) == 3
        assert all(r.trigger_query != "old query" for r in rows)
        assert repo.get(pseudo).embedded_at is not None
        assert repo.get(pseudo).last_error is None

    @pytest.mark.asyncio
    async def test_rule_vii_single_flight(self, tmp_path, sqlite_engine):
        """A4(vii): at most one in-flight refresh per pseudo-id."""
        root = str(tmp_path)
        write_skill(tmp_path, "tester", "sf-skill")
        embed = FakeEmbeddingService()
        svc = make_service(sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01)
        index_key = os.path.realpath(root)
        await svc.ensure_index(root, TEST_PROJECT_ID)
        meta = next(iter(svc._roots[index_key].metas.values()))
        # hammer the scheduler — single-flight collapses them
        for _ in range(10):
            svc._schedule_refresh_for(svc._roots[index_key], [meta])
        await asyncio.sleep(0.05)
        assert len(svc._inflight_refreshes) <= 1
        await asyncio.gather(*svc._refresh_tasks)
        assert embed.generate_calls.count("sf-skill") <= 2  # 1 initial + possible re-schedule, never 10


class TestGitPullPickup:
    @pytest.mark.asyncio
    async def test_content_change_picked_up_next_call(self, tmp_path, sqlite_engine):
        write_skill(tmp_path, "tester", "alpha-skill", body_text="Alpha body.\n")
        svc = make_service(sqlite_engine=sqlite_engine, refresh_debounce_s=0.01)
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        body_path = tmp_path / ".agents" / "tester" / "skills" / "alpha-skill" / "alpha-skill.md"
        old_digest = [
            m.digest
            for m in svc._roots[os.path.realpath(str(tmp_path))].metas.values()
            if m.name == "alpha-skill"
        ][0]
        # simulate a git pull touching the body
        time.sleep(0.01)
        body_path.write_text("Alpha body — PULLED.\n")
        stats = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert stats.changed == 1
        new_digest = [
            m.digest
            for m in svc._roots[os.path.realpath(str(tmp_path))].metas.values()
            if m.name == "alpha-skill"
        ][0]
        assert new_digest != old_digest
        corpus = await svc.corpus_for(TEST_PROJECT_ID, str(tmp_path))
        assert any(s.content == "Alpha body — PULLED.\n" for s in corpus)

    @pytest.mark.asyncio
    async def test_vanished_dir_dropped_with_rows_cleaned(self, canon_root, sqlite_engine):
        embed_repo = ProjectSkillEmbeddingRepository(sqlite_engine)
        svc = make_service(
            sqlite_engine=sqlite_engine,
            embedding_service=FakeEmbeddingService(),
            refresh_debounce_s=0.01,
        )
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        await asyncio.sleep(0.1)
        await asyncio.gather(*svc._refresh_tasks)
        index = svc._roots[os.path.realpath(canon_root)]
        victim = next(iter(index.metas.keys()))
        assert embed_repo.count_by_skill(victim) > 0
        shutil.rmtree(
            os.path.join(
                canon_root,
                ".agents",
                index.metas[victim].namespace,
                "skills",
                index.metas[victim].name,
            )
        )
        stats = await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        assert stats.vanished == 1
        assert victim not in svc._roots[os.path.realpath(canon_root)].metas
        # A4(iv): the vanished skill's embedding rows are gone
        assert embed_repo.count_by_skill(victim) == 0
        rows = ProjectSkillFileRepository(sqlite_engine).list_by_project(TEST_PROJECT_ID)
        assert victim not in {r.id for r in rows}

    @pytest.mark.asyncio
    async def test_zero_full_rescans_per_search_after_index(self, canon_root, sqlite_engine):
        """NFR-PERF-4: after the index, repeated searches cause no
        directory re-scan and no DB writes."""
        svc = make_service(sqlite_engine=sqlite_engine)
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        repo = ProjectSkillFileRepository(sqlite_engine)

        class CountingRepo:
            def __init__(self, inner):
                self.inner = inner
                self.calls = 0

            def sync_scan(self, *a, **k):
                self.calls += 1
                return self.inner.sync_scan(*a, **k)

            def __getattr__(self, name):
                return getattr(self.inner, name)

        counting = CountingRepo(repo)
        svc._files = counting
        for _ in range(5):
            stats = await svc.ensure_index(canon_root, TEST_PROJECT_ID)
            assert stats.full_scan is False
            assert stats.changed == 0 and stats.vanished == 0
            await svc.corpus_for(TEST_PROJECT_ID, canon_root)
        assert counting.calls == 0  # zero DB writes when nothing changed


class TestSentinelSupport:
    @pytest.mark.asyncio
    async def test_corpus_and_lookup_return_fresh_content(self, canon_root, sqlite_engine):
        svc = make_service(sqlite_engine=sqlite_engine)
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        corpus = await svc.corpus_for(TEST_PROJECT_ID, canon_root)
        assert all(s.content and s.root_path == os.path.realpath(canon_root) for s in corpus)
        hit = await svc.lookup_explicit("alpha-skill", TEST_PROJECT_ID)
        assert hit is not None and hit.content == "Alpha body.\n"
        by_address = await svc.lookup_explicit("pf:shared:gamma-skill", TEST_PROJECT_ID)
        assert by_address is not None and by_address.namespace == "shared"
        assert await svc.lookup_explicit("no-such-skill", TEST_PROJECT_ID) is None


class TestInvalidTransitionsAtStatGate:
    """Q7/D-Q2: the stat gate keeps ``valid``/``last_error`` rows truthful
    on transitions (valid→invalid, new-invalid dir, vanished-invalid dir)."""

    @pytest.mark.asyncio
    async def test_valid_to_invalid_updates_row_and_corpus(self, tmp_path, sqlite_engine):
        write_skill(tmp_path, "tester", "flip-skill", body_text="Fine body.\n")
        svc = make_service(sqlite_engine=sqlite_engine)
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        repo = ProjectSkillFileRepository(sqlite_engine)
        (row,) = repo.list_by_project(TEST_PROJECT_ID)
        assert row.valid is True and row.last_error is None

        # break the manifest (name mismatch) — stat gate must re-validate
        manifest_path = (
            tmp_path / ".agents" / "tester" / "skills" / "flip-skill" / "skill.json"
        )
        manifest = json.loads(manifest_path.read_text())
        manifest["name"] = "different-name"
        manifest_path.write_text(json.dumps(manifest))

        stats = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert stats.skills_indexed == 0 and stats.invalid_skills == 1
        (row,) = repo.list_by_project(TEST_PROJECT_ID)
        assert row.valid is False
        assert REASON_NAME_MISMATCH in (row.last_error or "")
        corpus = await svc.corpus_for(TEST_PROJECT_ID, str(tmp_path))
        assert corpus == []

    @pytest.mark.asyncio
    async def test_new_invalid_dir_gets_indexed_as_listed(self, tmp_path, sqlite_engine):
        write_skill(tmp_path, "tester", "good-skill")
        svc = make_service(sqlite_engine=sqlite_engine)
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        # a bad skill appears AFTER the index exists
        write_skill(tmp_path, "tester", "bad-skill", body_text="")
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        rows = ProjectSkillFileRepository(sqlite_engine).list_by_project(TEST_PROJECT_ID)
        by_name = {r.name: r for r in rows}
        assert by_name["good-skill"].valid is True
        assert by_name["bad-skill"].valid is False
        assert "body_empty" in (by_name["bad-skill"].last_error or "")

    @pytest.mark.asyncio
    async def test_vanished_invalid_dir_row_dropped(self, tmp_path, sqlite_engine):
        write_skill(tmp_path, "tester", "good-skill")
        write_skill(tmp_path, "tester", "bad-skill", body_text="")
        svc = make_service(sqlite_engine=sqlite_engine)
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        assert len(ProjectSkillFileRepository(sqlite_engine).list_by_project(TEST_PROJECT_ID)) == 2
        shutil.rmtree(tmp_path / ".agents" / "tester" / "skills" / "bad-skill")
        await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
        rows = ProjectSkillFileRepository(sqlite_engine).list_by_project(TEST_PROJECT_ID)
        assert {r.name for r in rows} == {"good-skill"}
        index = svc._roots[os.path.realpath(str(tmp_path))]
        assert index.invalid == []


class TestReadTimeSafety:
    """Task 9 acceptance: digest mismatch at read ⇒ serve fresh + re-index;
    missing body ⇒ explicit error envelope (never empty content)."""

    @pytest.mark.asyncio
    async def test_digest_mismatch_at_read_serves_fresh_and_schedules(
        self, tmp_path, sqlite_engine, caplog
    ):
        root = str(tmp_path)
        write_skill(tmp_path, "tester", "race-skill", body_text="Original.\n")
        embed = FakeEmbeddingService()
        svc = make_service(
            sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01
        )
        await svc.ensure_index(root, TEST_PROJECT_ID)
        index = svc._roots[os.path.realpath(root)]
        meta = next(iter(index.metas.values()))
        # mutate the body WITHOUT running the stat gate (the read race)
        (tmp_path / ".agents" / "tester" / "skills" / "race-skill" / "race-skill.md").write_text(
            "PULLED WHILE READING.\n"
        )
        with caplog.at_level("INFO"):
            content = await svc._read_body_fresh(os.path.realpath(root), meta)
        assert content == "PULLED WHILE READING.\n"  # canon wins, fresh served
        assert any("digest mismatch" in rec.message.lower() for rec in caplog.records)
        # re-index scheduled (fire-and-forget single-flight registered)
        assert meta.pseudo_id in svc._inflight_refreshes
        # The scheduled refresh waits for DIGEST STABILITY against the OLD
        # meta digest, which this test deliberately never reconciles —
        # cancel it rather than burning the bounded-backoff window.
        for task in list(svc._refresh_tasks):
            task.cancel()
        await asyncio.gather(*svc._refresh_tasks, return_exceptions=True)
        assert svc._inflight_refreshes == set()

    @pytest.mark.asyncio
    async def test_missing_body_raises_error_envelope(self, tmp_path, sqlite_engine):
        from daemon.services.project_skill_source.service import ProjectSkillBodyUnavailable

        root = str(tmp_path)
        write_skill(tmp_path, "tester", "gone-body")
        svc = make_service(sqlite_engine=sqlite_engine)
        await svc.ensure_index(root, TEST_PROJECT_ID)
        (tmp_path / ".agents" / "tester" / "skills" / "gone-body" / "gone-body.md").unlink()
        with pytest.raises(ProjectSkillBodyUnavailable):
            await svc.lookup_explicit("gone-body", TEST_PROJECT_ID)
        corpus = await svc.corpus_for(TEST_PROJECT_ID, root)
        assert corpus == []  # skipped, never served empty


class TestA4ViiDigestStability:
    @pytest.mark.asyncio
    async def test_unstable_digest_exhausts_debounce_and_skips(
        self, tmp_path, sqlite_engine, monkeypatch, caplog
    ):
        """A4(vii): refresh fires only after the digest is STABLE — a
        never-stabilizing digest exhausts the bounded backoff and writes
        NOTHING (branch toggling must not thrash MAIN-model calls)."""
        import daemon.services.project_skill_source.service as svc_mod

        monkeypatch.setattr(svc_mod, "EMBED_REFRESH_BACKOFF_MAX_S", 0.05)
        calls = {"n": 0}

        def always_changing(_root, _meta):
            calls["n"] += 1
            return f"digest-{calls['n']}"

        monkeypatch.setattr(svc_mod, "_current_digest", always_changing)
        root = str(tmp_path)
        write_skill(tmp_path, "tester", "unstable-skill")
        embed = FakeEmbeddingService()
        svc = make_service(
            sqlite_engine=sqlite_engine, embedding_service=embed, refresh_debounce_s=0.01
        )
        with caplog.at_level("WARNING"):
            await svc.ensure_index(root, TEST_PROJECT_ID)
            await asyncio.gather(*svc._refresh_tasks)
        assert embed.generate_calls == []  # never generated
        assert any("debounce window exhausted" in rec.message for rec in caplog.records)


class TestWarnOnceLifetime:
    @pytest.mark.asyncio
    async def test_unknown_schema_warns_once_per_root_across_gates(
        self, tmp_path, sqlite_engine, caplog
    ):
        """Q7: unknown-schema skips warn ONCE per root; the state lives on
        the root index entry, so later stat-gate re-validations log at
        DEBUG only (the invalid list stays operator-visible)."""
        bad = {**VALID_MANIFEST, "name": "x"}
        bad.pop("schema_version")
        write_skill(tmp_path, "tester", "x", manifest=bad)
        write_skill(tmp_path, "tester", "y", manifest={**bad, "name": "y"})
        svc = make_service(sqlite_engine=sqlite_engine)
        with caplog.at_level("DEBUG", logger="daemon.services.project_skill_source.service"):
            await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
            first = [
                rec for rec in caplog.records if "unknown-schema" in rec.message.lower()
            ]
            assert len(first) == 1, [r.message for r in first]
            assert first[0].levelname == "WARNING"

            # touch one manifest so the stat gate re-parses it
            manifest_path = tmp_path / ".agents" / "tester" / "skills" / "x" / "skill.json"
            manifest_path.write_text(json.dumps({**bad, "name": "x", "extra_touch": 1}))
            caplog.clear()
            stats = await svc.ensure_index(str(tmp_path), TEST_PROJECT_ID)
            assert stats.invalid_skills == 2
            warnings = [
                rec
                for rec in caplog.records
                if rec.levelno >= 30 and "unknown-schema" in rec.message.lower()
            ]
            assert warnings == []  # no repeat warning for this root
            debug_hits = [
                rec
                for rec in caplog.records
                if rec.levelno < 30 and "already warned" in rec.message.lower()
            ]
            assert debug_hits, "expected a DEBUG note for the re-validated skip"
        index = svc._roots[os.path.realpath(str(tmp_path))]
        assert index.invalid_schema_warned is True


class TestA16ServiceCache:
    @pytest.mark.asyncio
    async def test_cached_objects_expose_no_content(self, canon_root, sqlite_engine):
        """A16: the SERVICE's live cache holds metadata-only objects."""
        svc = make_service(sqlite_engine=sqlite_engine)
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        cached = svc.cached_objects()
        assert len(cached) == 3
        for obj in cached:
            assert not getattr(obj, "content", "")
            assert dataclasses.is_dataclass(obj)
            assert "content" not in {f.name for f in dataclasses.fields(obj)}


class TestReadTimeContainmentSwap:
    """A5(e): containment is RE-CHECKED on every load-time read — a
    mid-session symlink swap is refused even though the skill was indexed
    while still legitimate. Mutation-verified by the M5 spot-check."""

    @pytest.mark.asyncio
    async def test_symlink_swap_after_index_refused_at_read(self, tmp_path, sqlite_engine):
        from daemon.services.project_skill_source.service import ProjectSkillBodyUnavailable

        root = str(tmp_path)
        write_skill(tmp_path, "tester", "swap-skill", body_text="Legit body.\n")
        svc = make_service(sqlite_engine=sqlite_engine)
        await svc.ensure_index(root, TEST_PROJECT_ID)
        assert [s.name for s in await svc.corpus_for(TEST_PROJECT_ID, root)] == ["swap-skill"]

        # attacker swaps the skill dir for a symlink escaping the root
        outside = tmp_path / "outside-swap"
        outside.mkdir()
        (outside / "skill.json").write_text(
            json.dumps({**VALID_MANIFEST, "name": "swap-skill", "body": "swap-skill.md"})
        )
        (outside / "swap-skill.md").write_text("EVIL SWAPPED BODY.\n")
        skill_dir = tmp_path / ".agents" / "tester" / "skills" / "swap-skill"
        shutil.rmtree(skill_dir)
        skill_dir.symlink_to(outside, target_is_directory=True)

        with pytest.raises(ProjectSkillBodyUnavailable) as excinfo:
            await svc.lookup_explicit("swap-skill", TEST_PROJECT_ID)
        assert "symlink" in str(excinfo.value).lower() or "outside" in str(excinfo.value).lower()
        # the corpus also refuses to serve through the swapped path
        corpus = await svc.corpus_for(TEST_PROJECT_ID, root)
        assert corpus == []
