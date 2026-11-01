"""Unit pack: the resolver — D-Q1 rungs, A6 confirm-only arm, W-5
EXTRA_ROOTS binding, work_dir hook, drift WARNING, no-project invisibility.

Phase-1 task 5 acceptance: all 4 rungs + drift WARNING + the A6
explicit-conflict REFUSAL arm (asserted behavior, not
invisibility-by-accident) + no-project invisibility + the W-5
fail-closed extra-root leg.
"""

from __future__ import annotations

import os

import pytest

from daemon.services.project_skill_source.service import (
    EXTRA_ROOTS_ENV,
    WORK_DIR_METADATA_KEY,
)

from tests.helpers.project_skill_source_fixtures import (
    OTHER_PROJECT_ID,
    TEST_PROJECT_ID,
    FakeInstance,
    FakeInstanceRepository,
    FakeProject,
    FakeProjectRepository,
    make_service,
)


def _repos(main_dir=None, related=None, meta_project=None, row_project=None, work_dir=None):
    inst = FakeInstance(
        instance_id="inst-1",
        instance_metadata={
            **({"project_id": meta_project} if meta_project else {}),
            **({WORK_DIR_METADATA_KEY: work_dir} if work_dir else {}),
        },
        project_id=row_project,
    )
    project = FakeProject(
        project_id=TEST_PROJECT_ID, main_directory=main_dir, related_directories=related or []
    )
    return FakeInstanceRepository({"inst-1": inst}), FakeProjectRepository([project])


class TestResolverRungs:
    @pytest.mark.asyncio
    async def test_rung2_metadata_project_wins(self, tmp_path):
        inst_repo, proj_repo = _repos(
            main_dir=tmp_path, meta_project=TEST_PROJECT_ID, row_project=TEST_PROJECT_ID
        )
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        res = await svc.resolve_active_project("inst-1")
        assert res.project_id == TEST_PROJECT_ID  # metadata beats row on agreement-or-absence
        assert res.drift is None

    @pytest.mark.asyncio
    async def test_rung3_row_project_when_no_metadata(self, tmp_path):
        inst_repo, proj_repo = _repos(main_dir=tmp_path, row_project=TEST_PROJECT_ID)
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        res = await svc.resolve_active_project("inst-1")
        assert res.project_id == TEST_PROJECT_ID

    @pytest.mark.asyncio
    async def test_drift_proceeds_with_metadata_plus_warning(self, tmp_path, caplog):
        inst_repo, proj_repo = _repos(
            main_dir=tmp_path, meta_project=TEST_PROJECT_ID, row_project=OTHER_PROJECT_ID
        )
        # force disagreement: metadata present AND row different
        inst_repo.instances["inst-1"].project_id = OTHER_PROJECT_ID
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        with caplog.at_level("WARNING"):
            res = await svc.resolve_active_project("inst-1")
        assert res.project_id == TEST_PROJECT_ID  # metadata wins (D-Q1)
        assert res.drift == (TEST_PROJECT_ID, OTHER_PROJECT_ID)
        assert any("drift" in rec.message.lower() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_rung4_no_project_invisible(self):
        inst_repo, proj_repo = _repos()
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        res = await svc.resolve_active_project("inst-1")
        assert res.project_id is None
        assert res.roots == []
        assert not res.refused

    @pytest.mark.asyncio
    async def test_no_instance_id_invisible(self):
        svc = make_service(project_repo=FakeProjectRepository([]))
        res = await svc.resolve_active_project(None)
        assert res.project_id is None and res.roots == []


class TestA6ConfirmOnly:
    @pytest.mark.asyncio
    async def test_explicit_confirming_derived_is_accepted(self, tmp_path):
        inst_repo, proj_repo = _repos(main_dir=tmp_path, meta_project=TEST_PROJECT_ID)
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        res = await svc.resolve_active_project("inst-1", explicit_project_id=TEST_PROJECT_ID)
        assert res.project_id == TEST_PROJECT_ID
        assert res.roots == [os.path.realpath(str(tmp_path))]
        assert not res.refused

    @pytest.mark.asyncio
    async def test_explicit_conflict_refuses_file_corpus(self, tmp_path, caplog):
        """A6 MUST: explicit present and CONFLICTING ⇒ REFUSE the file
        corpus for that call (asserted refusal, DB results unaffected)."""
        inst_repo, proj_repo = _repos(main_dir=tmp_path, meta_project=TEST_PROJECT_ID)
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        with caplog.at_level("WARNING"):
            res = await svc.resolve_active_project(
                "inst-1", explicit_project_id=OTHER_PROJECT_ID
            )
        assert res.refused is True
        assert res.refusal_reason == "explicit_project_conflict"
        assert res.roots == []  # zero file skills from this call
        assert res.project_id == TEST_PROJECT_ID  # derived value still reported
        assert any("conflict" in rec.message.lower() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_explicit_with_no_instance_context_rest_shape_refuses(self, tmp_path):
        """The REST shape: no instance context, caller-supplied project —
        A6 ⇒ the file corpus is refused (REST serves ZERO file skills)."""
        proj_repo = FakeProjectRepository(
            [FakeProject(project_id=OTHER_PROJECT_ID, main_directory=str(tmp_path))]
        )
        svc = make_service(project_repo=proj_repo)
        res = await svc.resolve_active_project(None, explicit_project_id=OTHER_PROJECT_ID)
        assert res.refused is True
        assert res.roots == []


class TestW5ExtraRoots:
    @pytest.mark.asyncio
    async def test_bound_extra_root_consumed(self, tmp_path):
        extra = tmp_path / "wt-branch"
        (extra / ".agents" / "tester" / "skills").mkdir(parents=True)
        inst_repo, proj_repo = _repos(
            main_dir=tmp_path, related=[str(extra)], meta_project=TEST_PROJECT_ID
        )
        svc = make_service(
            instance_repo=inst_repo, project_repo=proj_repo, extra_roots=[str(extra)]
        )
        res = await svc.resolve_active_project("inst-1")
        assert os.path.realpath(str(extra)) in res.roots

    @pytest.mark.asyncio
    async def test_bound_via_related_directories_consumed(self, tmp_path):
        extra = tmp_path / "related-repo"
        extra.mkdir()
        inst_repo, proj_repo = _repos(
            main_dir=tmp_path, related=[str(extra)], meta_project=TEST_PROJECT_ID
        )
        svc = make_service(
            instance_repo=inst_repo, project_repo=proj_repo, extra_roots=[str(extra)]
        )
        res = await svc.resolve_active_project("inst-1")
        assert os.path.realpath(str(extra)) in res.roots

    @pytest.mark.asyncio
    async def test_unbound_entry_ignored_with_warning(self, tmp_path, caplog):
        other_repo = tmp_path / "some-other-project"
        other_repo.mkdir()
        inst_repo, proj_repo = _repos(main_dir=tmp_path, meta_project=TEST_PROJECT_ID)
        svc = make_service(
            instance_repo=inst_repo, project_repo=proj_repo, extra_roots=[str(other_repo)]
        )
        with caplog.at_level("WARNING"):
            res = await svc.resolve_active_project("inst-1")
        assert os.path.realpath(str(other_repo)) not in res.roots
        assert not res.refused  # ignored, not a refusal
        assert any("EXTRA_ROOTS" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_entry_pointing_at_other_project_refused(self, tmp_path):
        """The P3 fail-closed core: an EXTRA_ROOTS entry that resolves to
        ANOTHER project must never be consumed by this caller."""
        other_dir = tmp_path / "proj-b"
        other_dir.mkdir()
        repos = FakeProjectRepository(
            [
                FakeProject(project_id=TEST_PROJECT_ID, main_directory=str(tmp_path / "proj-a")),
                FakeProject(project_id=OTHER_PROJECT_ID, main_directory=str(other_dir)),
            ]
        )
        (tmp_path / "proj-a").mkdir()
        inst_repo = FakeInstanceRepository(
            {"inst-1": FakeInstance("inst-1", {"project_id": TEST_PROJECT_ID})}
        )
        svc = make_service(
            instance_repo=inst_repo, project_repo=repos, extra_roots=[str(other_dir)]
        )
        res = await svc.resolve_active_project("inst-1")
        assert os.path.realpath(str(other_dir)) not in res.roots

    @pytest.mark.asyncio
    async def test_relative_entry_ignored(self, tmp_path):
        inst_repo, proj_repo = _repos(main_dir=tmp_path, meta_project=TEST_PROJECT_ID)
        svc = make_service(project_repo=proj_repo, extra_roots=["relative/path"])
        res = await svc.resolve_active_project("inst-1")
        assert all(os.path.isabs(r) for r in res.roots)


class TestRootChain:
    @pytest.mark.asyncio
    async def test_main_directory_root(self, tmp_path):
        inst_repo, proj_repo = _repos(main_dir=tmp_path, meta_project=TEST_PROJECT_ID)
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        res = await svc.resolve_active_project("inst-1")
        assert res.roots == [os.path.realpath(str(tmp_path))]

    @pytest.mark.asyncio
    async def test_missing_main_directory_graceful_zero(self):
        inst_repo, proj_repo = _repos(
            main_dir="/nonexistent-path-xyz", meta_project=TEST_PROJECT_ID
        )
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        res = await svc.resolve_active_project("inst-1")
        assert res.project_id == TEST_PROJECT_ID
        assert res.roots == []  # graceful zero — never an error

    @pytest.mark.asyncio
    async def test_work_dir_bound_hook_consumed(self, tmp_path):
        wt = tmp_path / "worktree"
        wt.mkdir()
        inst_repo, proj_repo = _repos(
            main_dir=tmp_path,
            meta_project=TEST_PROJECT_ID,
            work_dir=str(wt),
        )
        # bind via related_directories so the hook's binding predicate passes
        proj_repo.projects[0].related_directories = [str(wt)]
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        res = await svc.resolve_active_project("inst-1")
        assert os.path.realpath(str(wt)) in res.roots

    @pytest.mark.asyncio
    async def test_work_dir_unbound_ignored_with_warning(self, tmp_path, caplog):
        rogue = tmp_path / "rogue-repo"
        rogue.mkdir()
        inst_repo, proj_repo = _repos(
            main_dir=tmp_path, meta_project=TEST_PROJECT_ID, work_dir=str(rogue)
        )
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        with caplog.at_level("WARNING"):
            res = await svc.resolve_active_project("inst-1")
        assert os.path.realpath(str(rogue)) not in res.roots
        assert any("work_dir" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_no_repo_projects_graceful_db_only(self):
        """Non-repo projects (main_directory NULL) — graceful zero, no
        error (FR-P3-6)."""
        inst_repo = FakeInstanceRepository(
            {"inst-1": FakeInstance("inst-1", {"project_id": TEST_PROJECT_ID})}
        )
        proj_repo = FakeProjectRepository([FakeProject(project_id=TEST_PROJECT_ID)])
        svc = make_service(instance_repo=inst_repo, project_repo=proj_repo)
        res = await svc.resolve_active_project("inst-1")
        assert res.project_id == TEST_PROJECT_ID and res.roots == []
