"""Tests for :class:`SkillStoreService` (Phase 2 of Skill Evolution).

The service is the async facade over the Phase 1 repositories
(:class:`SkillRepository`, :class:`SkillLineageRepository`) plus
the Phase 2 :class:`SkillEmbeddingService`. These tests cover:

* **CRUD with embedding refresh** — :meth:`create_skill` and
  :meth:`update_skill` invoke the embedding service, and the
  embedding call is best-effort (failure must NOT abort the
  underlying CRUD).
* **Project-scope filtering** — :meth:`list_skills` returns both
  project-scoped and global skills for a given project, and only
  global skills when ``project_id=None``.
* **View bundling** — :meth:`view_skill` ships the full skill
  document plus its lineage graph in one round-trip.
* **Embedding failure isolation** — when the embedding pipeline
  raises, the service logs a warning and returns the
  successfully-created/updated skill anyway.

All repository and embedding-service calls are mocked at the
boundary so the tests run without a database or an OpenAI
endpoint.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.skill_store_service import (
    SkillStoreService,
    _project_skill,
)


# ============================================================
# Fixtures / helpers
# ============================================================


def make_skill(
    *,
    skill_id: str = "skill-abc",
    name: str = "code-review",
    description: str = "Review code for bugs and style.",
    content: str = "# Code Review\n\nCheck correctness, performance.",
    project_id: str | None = "proj-1",
    category: str = "workflow",
    status: str = "active",
    lineage_origin: str = "imported",
    generation: int = 0,
    created_at: str = "2026-01-01T00:00:00+00:00",
    updated_at: str = "2026-01-01T00:00:00+00:00",
) -> MagicMock:
    """Build a mock :class:`Skill` exposing the attributes the service reads.

    Uses ``MagicMock(spec=[...])`` with the exact attribute names so
    the test fails fast when the service touches an unexpected
    column — cheaper than silently passing.
    """
    skill = MagicMock(
        spec=[
            "id",
            "name",
            "description",
            "content",
            "project_id",
            "category",
            "status",
            "lineage_origin",
            "generation",
            "created_at",
            "updated_at",
        ]
    )
    skill.id = skill_id
    skill.name = name
    skill.description = description
    skill.content = content
    skill.project_id = project_id
    skill.category = category
    skill.status = status
    skill.lineage_origin = lineage_origin
    skill.generation = generation
    skill.created_at = created_at
    skill.updated_at = updated_at
    return skill


def make_skill_repo() -> MagicMock:
    """Build a mock :class:`SkillRepository`.

    Defaults every method to a reasonable stub. Tests override
    the methods they care about (``create``, ``list``,
    ``update``, etc.) on a per-test basis.
    """
    repo = MagicMock()
    repo.create = MagicMock(side_effect=lambda **kwargs: make_skill(**kwargs))
    repo.get = MagicMock(return_value=None)
    repo.list = MagicMock(return_value=([], 0))
    repo.update = MagicMock(return_value=None)
    repo.delete = MagicMock(return_value=False)
    repo.deactivate = MagicMock(return_value=None)
    return repo


def make_lineage_repo() -> MagicMock:
    """Build a mock :class:`SkillLineageRepository`.

    Defaults ``get_parents`` and ``get_children`` to empty lists.
    """
    repo = MagicMock()
    repo.get_parents = MagicMock(return_value=[])
    repo.get_children = MagicMock(return_value=[])
    return repo


def make_embedding_service(
    *,
    side_effect: Any = None,
) -> MagicMock:
    """Build a mock :class:`SkillEmbeddingService`.

    ``update_skill_embeddings`` is the only method the service
    calls; it's async, so we use ``AsyncMock``. ``side_effect``
    lets a test inject a raising behavior to exercise the
    graceful-degradation path.
    """
    svc = MagicMock()
    svc.update_skill_embeddings = AsyncMock(
        return_value=3,
        side_effect=side_effect,
    )
    return svc


def make_service(
    *,
    skill_repo: MagicMock | None = None,
    lineage_repo: MagicMock | None = None,
    embedding_service: MagicMock | None = None,
) -> SkillStoreService:
    """Build a :class:`SkillStoreService` with mock dependencies."""
    return SkillStoreService(
        skill_repo=skill_repo if skill_repo is not None else make_skill_repo(),
        lineage_repo=(
            lineage_repo if lineage_repo is not None else make_lineage_repo()
        ),
        embedding_service=(
            embedding_service
            if embedding_service is not None
            else make_embedding_service()
        ),
    )


# ============================================================
# TestCreateSkill
# ============================================================


class TestCreateSkill:
    """:meth:`SkillStoreService.create_skill` — happy path and embedding failure."""

    @pytest.mark.asyncio
    async def test_creates_skill_via_repo(self):
        """The repo's ``create`` is called with the right kwargs."""
        repo = make_skill_repo()
        repo.create = MagicMock(
            return_value=make_skill(skill_id="skill-new")
        )
        service = make_service(skill_repo=repo)

        result = await service.create_skill(
            name="my-skill",
            description="Does X.",
            content="# X\n\nbody",
            project_id="proj-7",
            category="domain",
            lineage_origin="feedback",
        )

        # Repo was called with the explicit kwargs + ``status="active"``.
        repo.create.assert_called_once_with(
            name="my-skill",
            description="Does X.",
            content="# X\n\nbody",
            project_id="proj-7",
            category="domain",
            lineage_origin="feedback",
            status="active",
        )
        assert result.id == "skill-new"

    @pytest.mark.asyncio
    async def test_refreshes_embeddings_after_create(self):
        """The embedding service is awaited with the new skill."""
        repo = make_skill_repo()
        repo.create = MagicMock(
            return_value=make_skill(skill_id="skill-new")
        )
        embedding_service = make_embedding_service()
        service = make_service(
            skill_repo=repo, embedding_service=embedding_service
        )

        await service.create_skill(
            name="s", description="d", content="c",
        )

        embedding_service.update_skill_embeddings.assert_awaited_once()
        # The skill passed to the embedding service is the
        # one returned by the repo.
        passed_skill = (
            embedding_service.update_skill_embeddings.await_args.args[0]
        )
        assert passed_skill.id == "skill-new"

    @pytest.mark.asyncio
    async def test_embedding_failure_does_not_abort_create(self):
        """An exception from the embedding service is caught; create still succeeds."""
        repo = make_skill_repo()
        repo.create = MagicMock(
            return_value=make_skill(skill_id="skill-graceful")
        )
        embedding_service = make_embedding_service(
            side_effect=RuntimeError("OpenAI down")
        )
        service = make_service(
            skill_repo=repo, embedding_service=embedding_service
        )

        # Must NOT raise — the skill is usable BM25-only.
        result = await service.create_skill(
            name="s", description="d", content="c",
        )

        assert result.id == "skill-graceful"
        embedding_service.update_skill_embeddings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_logs_warning_on_embedding_failure(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Embedding failure produces a WARNING log line, not a CRITICAL."""
        import logging

        repo = make_skill_repo()
        repo.create = MagicMock(return_value=make_skill(skill_id="skill-1"))
        embedding_service = make_embedding_service(
            side_effect=RuntimeError("API key missing")
        )
        service = make_service(
            skill_repo=repo, embedding_service=embedding_service
        )

        with caplog.at_level(logging.WARNING, logger="daemon.services.skill_store_service"):
            result = await service.create_skill(
                name="s", description="d", content="c",
            )

        # Skill is still created successfully.
        assert result.id == "skill-1"
        # And a warning was emitted.
        assert any(
            "Embedding refresh failed" in rec.message
            and "skill-1" in rec.message
            for rec in caplog.records
        ), f"Expected embedding-failure warning, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_create_with_project_id_none(self):
        """A ``project_id=None`` (global) skill is created."""
        repo = make_skill_repo()
        repo.create = MagicMock(
            return_value=make_skill(project_id=None)
        )
        service = make_service(skill_repo=repo)

        result = await service.create_skill(
            name="global-skill",
            description="global",
            content="# body",
            project_id=None,
        )

        assert result.project_id is None
        repo.create.assert_called_once()
        assert (
            repo.create.call_args.kwargs["project_id"] is None
        )


# ============================================================
# TestGetSkill
# ============================================================


class TestGetSkill:
    """:meth:`SkillStoreService.get_skill` — passthrough to ``SkillRepository.get``."""

    @pytest.mark.asyncio
    async def test_returns_skill_when_found(self):
        repo = make_skill_repo()
        repo.get = MagicMock(return_value=make_skill(skill_id="skill-1"))
        service = make_service(skill_repo=repo)

        result = await service.get_skill("skill-1")

        assert result is not None
        assert result.id == "skill-1"
        repo.get.assert_called_once_with("skill-1")

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        repo = make_skill_repo()
        repo.get = MagicMock(return_value=None)
        service = make_service(skill_repo=repo)

        result = await service.get_skill("skill-missing")

        assert result is None


# ============================================================
# TestListSkills
# ============================================================


class TestListSkills:
    """:meth:`SkillStoreService.list_skills` — project-scope filtering + projection."""

    @pytest.mark.asyncio
    async def test_global_only_when_project_id_is_none(self):
        """``project_id=None`` returns only ``SkillRepository.list(project_id=None)``."""
        repo = make_skill_repo()
        global_skill = make_skill(skill_id="g-1", project_id=None)
        repo.list = MagicMock(return_value=([global_skill], 1))
        service = make_service(skill_repo=repo)

        items, total = await service.list_skills(project_id=None)

        # Exactly one repo call — the global bucket.
        repo.list.assert_called_once_with(
            project_id=None,
            active_only=True,
            limit=100,
            offset=0,
        )
        assert total == 1
        assert len(items) == 1
        assert items[0]["id"] == "g-1"

    @pytest.mark.asyncio
    async def test_project_scope_includes_global_skills(self):
        """A project scope returns project skills AND global skills."""
        repo = make_skill_repo()
        proj_skill = make_skill(skill_id="p-1", project_id="proj-A")
        global_skill = make_skill(skill_id="g-1", project_id=None)

        # First call (project): 1 project skill. Second call (global): 1 global.
        repo.list = MagicMock(
            side_effect=[
                ([proj_skill], 1),
                ([global_skill], 1),
            ]
        )
        service = make_service(skill_repo=repo)

        items, total = await service.list_skills(project_id="proj-A")

        # Both repo calls happened, both with the same active/limit/offset.
        assert repo.list.call_count == 2
        first_call_kwargs = repo.list.call_args_list[0].kwargs
        second_call_kwargs = repo.list.call_args_list[1].kwargs
        assert first_call_kwargs["project_id"] == "proj-A"
        assert second_call_kwargs["project_id"] is None

        # Total is the sum across both buckets.
        assert total == 2
        # Both skills are present in the merged list.
        ids = {item["id"] for item in items}
        assert ids == {"p-1", "g-1"}

    @pytest.mark.asyncio
    async def test_active_only_is_passed_through(self):
        """``active_only=False`` propagates to the repo."""
        repo = make_skill_repo()
        repo.list = MagicMock(return_value=([], 0))
        service = make_service(skill_repo=repo)

        await service.list_skills(
            project_id=None, active_only=False, limit=10, offset=5
        )

        repo.list.assert_called_once_with(
            project_id=None,
            active_only=False,
            limit=10,
            offset=5,
        )

    @pytest.mark.asyncio
    async def test_list_skills_projection_strips_content(self):
        """The list-shape dicts exclude ``content`` (and counters)."""
        repo = make_skill_repo()
        skill = make_skill(skill_id="s-1", project_id=None)
        repo.list = MagicMock(return_value=([skill], 1))
        service = make_service(skill_repo=repo)

        items, _total = await service.list_skills(project_id=None)

        assert len(items) == 1
        projection = items[0]
        # Required fields.
        assert projection["id"] == "s-1"
        assert projection["name"] == "code-review"
        assert projection["description"] == "Review code for bugs and style."
        assert projection["category"] == "workflow"
        assert projection["status"] == "active"
        assert projection["created_at"] == "2026-01-01T00:00:00+00:00"
        assert projection["updated_at"] == "2026-01-01T00:00:00+00:00"
        # Stripped fields.
        assert "content" not in projection
        assert "total_selections" not in projection
        assert "total_applied" not in projection

    @pytest.mark.asyncio
    async def test_list_skills_empty_when_nothing_matches(self):
        """Empty list / ``total=0`` is forwarded cleanly."""
        repo = make_skill_repo()
        repo.list = MagicMock(return_value=([], 0))
        service = make_service(skill_repo=repo)

        items, total = await service.list_skills(project_id=None)

        assert items == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_skills_empty_when_only_project_scope_empty(self):
        """Empty project + empty global → empty result + total 0."""
        repo = make_skill_repo()
        repo.list = MagicMock(side_effect=[([], 0), ([], 0)])
        service = make_service(skill_repo=repo)

        items, total = await service.list_skills(project_id="proj-X")

        assert items == []
        assert total == 0


# ============================================================
# TestUpdateSkill
# ============================================================


class TestUpdateSkill:
    """:meth:`SkillStoreService.update_skill` — embedding refresh on content change."""

    @pytest.mark.asyncio
    async def test_update_calls_repo_with_fields(self):
        repo = make_skill_repo()
        repo.update = MagicMock(
            return_value=make_skill(skill_id="s-1", description="new")
        )
        service = make_service(skill_repo=repo)

        result = await service.update_skill("s-1", description="new")

        repo.update.assert_called_once_with("s-1", description="new")
        assert result is not None

    @pytest.mark.asyncio
    async def test_update_returns_none_when_skill_missing(self):
        repo = make_skill_repo()
        repo.update = MagicMock(return_value=None)
        service = make_service(skill_repo=repo)

        result = await service.update_skill("missing", description="x")

        assert result is None

    @pytest.mark.asyncio
    async def test_content_change_triggers_embedding_refresh(self):
        """Updating ``content`` triggers the embedding pipeline."""
        repo = make_skill_repo()
        repo.update = MagicMock(
            return_value=make_skill(skill_id="s-1")
        )
        embedding_service = make_embedding_service()
        service = make_service(
            skill_repo=repo, embedding_service=embedding_service
        )

        await service.update_skill("s-1", content="new body")

        embedding_service.update_skill_embeddings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_content_unchanged_skips_embedding_refresh(self):
        """Updating only metadata does NOT trigger the embedding pipeline."""
        repo = make_skill_repo()
        repo.update = MagicMock(
            return_value=make_skill(skill_id="s-1", description="new")
        )
        embedding_service = make_embedding_service()
        service = make_service(
            skill_repo=repo, embedding_service=embedding_service
        )

        await service.update_skill("s-1", description="new")

        embedding_service.update_skill_embeddings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_embedding_failure_does_not_abort_update(self):
        """An exception from the embedding service is caught; update still succeeds."""
        repo = make_skill_repo()
        repo.update = MagicMock(return_value=make_skill(skill_id="s-1"))
        embedding_service = make_embedding_service(
            side_effect=RuntimeError("OpenAI down")
        )
        service = make_service(
            skill_repo=repo, embedding_service=embedding_service
        )

        # Must NOT raise.
        result = await service.update_skill("s-1", content="new body")

        assert result is not None
        embedding_service.update_skill_embeddings.assert_awaited_once()


# ============================================================
# TestDeleteSkill
# ============================================================


class TestDeleteSkill:
    """:meth:`SkillStoreService.delete_skill` — passthrough to repo."""

    @pytest.mark.asyncio
    async def test_delete_returns_true_when_row_existed(self):
        repo = make_skill_repo()
        repo.delete = MagicMock(return_value=True)
        service = make_service(skill_repo=repo)

        result = await service.delete_skill("s-1")

        assert result is True
        repo.delete.assert_called_once_with("s-1")

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_row_missing(self):
        repo = make_skill_repo()
        repo.delete = MagicMock(return_value=False)
        service = make_service(skill_repo=repo)

        result = await service.delete_skill("missing")

        assert result is False


# ============================================================
# TestDeactivateSkill
# ============================================================


class TestDeactivateSkill:
    """:meth:`SkillStoreService.deactivate_skill` — passthrough to repo."""

    @pytest.mark.asyncio
    async def test_deactivate_returns_updated_skill(self):
        repo = make_skill_repo()
        deactivated = make_skill(skill_id="s-1", status="inactive")
        repo.deactivate = MagicMock(return_value=deactivated)
        service = make_service(skill_repo=repo)

        result = await service.deactivate_skill("s-1")

        repo.deactivate.assert_called_once_with("s-1")
        assert result is not None
        assert result.status == "inactive"

    @pytest.mark.asyncio
    async def test_deactivate_returns_none_when_missing(self):
        repo = make_skill_repo()
        repo.deactivate = MagicMock(return_value=None)
        service = make_service(skill_repo=repo)

        result = await service.deactivate_skill("missing")

        assert result is None


# ============================================================
# TestViewSkill
# ============================================================


class TestViewSkill:
    """:meth:`SkillStoreService.view_skill` — bundles skill + lineage."""

    @pytest.mark.asyncio
    async def test_view_returns_skill_and_lineage(self):
        """The view bundles the full skill row plus both lineage lists."""
        repo = make_skill_repo()
        full_skill = make_skill(skill_id="s-1")
        # Make ``to_dict`` work on the mock.
        full_skill.to_dict = MagicMock(
            return_value={
                "id": "s-1",
                "name": "code-review",
                "content": "# body",
                # …all other fields…
                "_full": True,
            }
        )
        repo.get = MagicMock(return_value=full_skill)

        lineage_repo = make_lineage_repo()
        parent_edge = MagicMock()
        parent_edge.to_dict = MagicMock(
            return_value={"skill_id": "s-1", "parent_skill_id": "s-0"}
        )
        child_edge = MagicMock()
        child_edge.to_dict = MagicMock(
            return_value={"skill_id": "s-2", "parent_skill_id": "s-1"}
        )
        lineage_repo.get_parents = MagicMock(return_value=[parent_edge])
        lineage_repo.get_children = MagicMock(return_value=[child_edge])

        service = make_service(
            skill_repo=repo, lineage_repo=lineage_repo
        )

        result = await service.view_skill("s-1")

        assert result is not None
        assert "skill" in result
        assert "lineage" in result
        # Skill body is full (via to_dict).
        assert result["skill"]["_full"] is True
        # Lineage is split into parents/children.
        assert len(result["lineage"]["parents"]) == 1
        assert result["lineage"]["parents"][0]["parent_skill_id"] == "s-0"
        assert len(result["lineage"]["children"]) == 1
        assert result["lineage"]["children"][0]["parent_skill_id"] == "s-1"

    @pytest.mark.asyncio
    async def test_view_returns_none_when_skill_missing(self):
        repo = make_skill_repo()
        repo.get = MagicMock(return_value=None)
        service = make_service(skill_repo=repo)

        result = await service.view_skill("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_view_with_empty_lineage(self):
        """A skill with no parents or children still returns the lineage key."""
        repo = make_skill_repo()
        full_skill = make_skill(skill_id="s-1")
        full_skill.to_dict = MagicMock(return_value={"id": "s-1"})
        repo.get = MagicMock(return_value=full_skill)
        lineage_repo = make_lineage_repo()  # default empty lists
        service = make_service(
            skill_repo=repo, lineage_repo=lineage_repo
        )

        result = await service.view_skill("s-1")

        assert result is not None
        assert result["lineage"] == {"parents": [], "children": []}


# ============================================================
# TestConstructor
# ============================================================


class TestConstructor:
    """The constructor stores dependencies as attributes."""

    def test_stores_skill_repo(self):
        repo = make_skill_repo()
        service = make_service(skill_repo=repo)
        assert service._skill_repo is repo

    def test_stores_lineage_repo(self):
        lineage = make_lineage_repo()
        service = make_service(lineage_repo=lineage)
        assert service._lineage_repo is lineage

    def test_stores_embedding_service(self):
        embedding = make_embedding_service()
        service = make_service(embedding_service=embedding)
        assert service._embedding_service is embedding


# ============================================================
# TestProjectSkillHelper
# ============================================================


class TestProjectSkillHelper:
    """Pin the ``_project_skill`` helper used by ``list_skills``."""

    def test_projects_all_list_fields(self):
        skill = make_skill()
        projected = _project_skill(skill)
        assert projected == {
            "id": "skill-abc",
            "name": "code-review",
            "description": "Review code for bugs and style.",
            "category": "workflow",
            "status": "active",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    def test_projection_excludes_content(self):
        """The big ``content`` field is intentionally stripped."""
        skill = make_skill(content="X" * 10_000)
        projected = _project_skill(skill)
        assert "content" not in projected