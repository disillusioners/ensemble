"""Comprehensive unit tests for all six Skill Evolution repositories.

Phase 1 of the Skill Evolution System — covers:

* :class:`SkillRepository` — CRUD, counter increments, A/B variants, BM25 search.
* :class:`SkillLineageRepository` — parent/child DAG.
* :class:`SkillUsageRepository` — usage events, stats, feedback.
* :class:`SkillTriggerRepository` — condition/action rules.
* :class:`SkillEmbeddingRepository` — cached vector embeddings.
* :class:`SkillABTestRepository` — A/B test lifecycle.

All tests run against an in-memory SQLite database (via the
``engine`` fixture in ``tests/repositories/conftest.py``) so they are
fast, isolated, and produce no on-disk artifacts. The fixtures
``project_id`` and ``other_project_id`` provide stable IDs for the
project-scoping tests.
"""

from __future__ import annotations

import pytest


# =============================================================================
# Helpers
# =============================================================================


def _make_skill(repo, project_id, name, **kwargs):
    """Helper to create a Skill with sensible defaults."""
    defaults = {
        "name": name,
        "description": f"desc for {name}",
        "content": f"content for {name}",
        "project_id": project_id,
    }
    defaults.update(kwargs)
    return repo.create(**defaults)


# =============================================================================
# SkillRepository — CREATE
# =============================================================================


class TestCreateSkill:
    """Tests for :meth:`SkillRepository.create`."""

    def test_create_skill(self, skill_repo, project_id):
        """Create a skill, verify all key fields."""
        skill = skill_repo.create(
            name="workflow-debug",
            description="Debug workflow skill",
            content="# Debug\nStep 1: ...",
            project_id=project_id,
            category="workflow",
            lineage_origin="imported",
            generation=0,
        )

        assert skill.id is not None
        assert skill.name == "workflow-debug"
        assert skill.description == "Debug workflow skill"
        assert skill.content == "# Debug\nStep 1: ..."
        assert skill.project_id == project_id
        assert skill.category == "workflow"
        assert skill.lineage_origin == "imported"
        assert skill.generation == 0
        assert skill.is_active is True
        assert skill.status == "active"
        assert skill.total_selections == 0
        assert skill.total_applied == 0
        assert skill.total_completions == 0
        assert skill.total_fallbacks == 0
        assert skill.consecutive_failures == 0
        assert skill.created_at is not None
        assert skill.updated_at is not None
        assert skill.last_used_at is None

    def test_create_skill_kwargs_forwarded(self, skill_repo, project_id):
        """Extra kwargs (e.g. ``ab_test_group``) are forwarded to the model."""
        skill = skill_repo.create(
            name="ab-test-skill",
            description="A/B test skill",
            content="content",
            project_id=project_id,
            ab_test_group="group-abc",
        )
        assert skill.ab_test_group == "group-abc"

    def test_create_global_skill_project_id_none(self, skill_repo):
        """A skill with ``project_id=None`` is a global skill."""
        skill = skill_repo.create(
            name="global-skill",
            description="Global",
            content="content",
            project_id=None,
        )
        assert skill.project_id is None


# =============================================================================
# SkillRepository — READ
# =============================================================================


class TestGetSkill:
    """Tests for :meth:`SkillRepository.get`."""

    def test_get_skill(self, skill_repo, project_id):
        """Create then get by ID returns the same row."""
        created = _make_skill(skill_repo, project_id, "alpha")
        fetched = skill_repo.get(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "alpha"
        assert fetched.project_id == project_id

    def test_get_skill_nonexistent(self, skill_repo):
        """get() on a non-existent ID returns None."""
        assert skill_repo.get("does-not-exist") is None


class TestGetByName:
    """Tests for :meth:`SkillRepository.get_by_name`."""

    def test_get_by_name(self, skill_repo, project_id):
        """Create then get_by_name(project_id, name, generation) returns the row."""
        created = _make_skill(skill_repo, project_id, "beta")
        fetched = skill_repo.get_by_name(project_id, "beta", 0)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "beta"

    def test_get_by_name_wrong_generation(self, skill_repo, project_id):
        """get_by_name with wrong generation returns None."""
        _make_skill(skill_repo, project_id, "beta")
        fetched = skill_repo.get_by_name(project_id, "beta", 99)
        assert fetched is None

    def test_get_by_name_wrong_project(self, skill_repo, project_id, other_project_id):
        """get_by_name with wrong project_id returns None."""
        _make_skill(skill_repo, project_id, "beta")
        fetched = skill_repo.get_by_name(other_project_id, "beta", 0)
        assert fetched is None


class TestListSkills:
    """Tests for :meth:`SkillRepository.list`."""

    def test_list_skills(self, skill_repo, project_id, other_project_id):
        """List returns only the requested project's skills."""
        _make_skill(skill_repo, project_id, "a")
        _make_skill(skill_repo, project_id, "b")
        _make_skill(skill_repo, other_project_id, "c")

        items, total = skill_repo.list(project_id=project_id)

        assert total == 2
        assert len(items) == 2
        names = {s.name for s in items}
        assert names == {"a", "b"}

    def test_list_skills_pagination(self, skill_repo, project_id):
        """List respects limit/offset."""
        for i in range(5):
            _make_skill(skill_repo, project_id, f"s{i}")

        page1, total1 = skill_repo.list(project_id=project_id, limit=2, offset=0)
        page2, total2 = skill_repo.list(project_id=project_id, limit=2, offset=2)

        assert total1 == 5
        assert total2 == 5
        assert len(page1) == 2
        assert len(page2) == 2
        page1_ids = {s.id for s in page1}
        page2_ids = {s.id for s in page2}
        assert page1_ids.isdisjoint(page2_ids)

    def test_list_skills_active_only_filter(self, skill_repo, project_id):
        """``active_only=False`` returns deactivated skills too."""
        active = _make_skill(skill_repo, project_id, "active")
        inactive = _make_skill(skill_repo, project_id, "inactive")
        skill_repo.deactivate(inactive.id)

        # Default: active_only=True
        items_active, total_active = skill_repo.list(project_id=project_id)
        assert total_active == 1
        assert items_active[0].id == active.id

        # active_only=False: both rows visible
        items_all, total_all = skill_repo.list(
            project_id=project_id, active_only=False
        )
        assert total_all == 2
        assert {s.id for s in items_all} == {active.id, inactive.id}


# =============================================================================
# SkillRepository — UPDATE
# =============================================================================


class TestUpdateSkill:
    """Tests for :meth:`SkillRepository.update`."""

    def test_update_skill(self, skill_repo, project_id):
        """Update fields on a skill."""
        skill = _make_skill(skill_repo, project_id, "gamma")
        original_created_at = skill.created_at

        updated = skill_repo.update(
            skill.id,
            description="new description",
            category="prompt",
        )

        assert updated is not None
        assert updated.description == "new description"
        assert updated.category == "prompt"
        # created_at is protected and must not change.
        assert updated.created_at == original_created_at
        # updated_at is bumped on every update.
        assert updated.updated_at >= original_created_at

    def test_update_skill_unknown_field_raises(self, skill_repo, project_id):
        """Updating with a non-existent field raises ``AttributeError``."""
        skill = _make_skill(skill_repo, project_id, "gamma")
        with pytest.raises(AttributeError):
            skill_repo.update(skill.id, no_such_field="x")

    def test_update_skill_protected_fields_silently_dropped(
        self, skill_repo, project_id, caplog
    ):
        """``id`` and ``created_at`` are silently dropped from updates."""
        skill = _make_skill(skill_repo, project_id, "gamma")
        original_id = skill.id
        original_created_at = skill.created_at

        with caplog.at_level("WARNING"):
            updated = skill_repo.update(
                skill.id,
                id="new-id-attempt",
                created_at="2020-01-01T00:00:00+00:00",
                description="kept",
            )

        assert updated is not None
        assert updated.id == original_id
        assert updated.created_at == original_created_at
        assert updated.description == "kept"
        # Warnings emitted for each protected field.
        warned = {
            rec.getMessage().split("field=")[-1]
            for rec in caplog.records
            if "Ignoring protected field" in rec.getMessage()
        }
        assert {"id", "created_at"} <= warned

    def test_update_skill_nonexistent_returns_none(self, skill_repo):
        """Update on a non-existent ID returns None."""
        assert skill_repo.update("ghost-id", name="x") is None


# =============================================================================
# SkillRepository — DELETE
# =============================================================================


class TestDeleteSkill:
    """Tests for :meth:`SkillRepository.delete`."""

    def test_delete_skill(self, skill_repo, project_id):
        """delete() removes the row and get() returns None afterwards."""
        skill = _make_skill(skill_repo, project_id, "delta")

        assert skill_repo.delete(skill.id) is True
        assert skill_repo.get(skill.id) is None

    def test_delete_skill_nonexistent(self, skill_repo):
        """delete() on a non-existent ID returns False."""
        assert skill_repo.delete("ghost-id") is False

    def test_delete_skill_cascades_lineage_and_embeddings(
        self, skill_repo, embedding_repo, lineage_repo, project_id
    ):
        """Deleting a skill cascades through its FKs (lineage, embeddings)."""
        parent = _make_skill(skill_repo, project_id, "parent")
        child = _make_skill(
            skill_repo, project_id, "child", generation=1
        )

        # Set up lineage edge (parent → child).
        lineage_repo.create(skill_id=child.id, parent_skill_id=parent.id)

        # Set up an embedding on each.
        embedding_repo.create(
            skill_id=parent.id,
            trigger_query="parent q",
            embedding=[0.1, 0.2, 0.3],
        )
        embedding_repo.create(
            skill_id=child.id,
            trigger_query="child q",
            embedding=[0.4, 0.5, 0.6],
        )

        assert len(lineage_repo.get_parents(child.id)) == 1
        assert len(embedding_repo.get_by_skill(parent.id)) == 1

        # Deleting the parent cascades to its lineage edge AND its
        # embedding rows.
        assert skill_repo.delete(parent.id) is True
        assert skill_repo.get(parent.id) is None
        assert embedding_repo.get_by_skill(parent.id) == []


# =============================================================================
# SkillRepository — DEACTIVATE
# =============================================================================


class TestDeactivateSkill:
    """Tests for :meth:`SkillRepository.deactivate`."""

    def test_deactivate_skill(self, skill_repo, project_id):
        """deactivate() sets ``is_active=False`` and ``status='inactive'``."""
        skill = _make_skill(skill_repo, project_id, "echo")

        deactivated = skill_repo.deactivate(skill.id)

        assert deactivated is not None
        assert deactivated.is_active is False
        assert deactivated.status == "inactive"

        # And the change is visible via ``get()``.
        fetched = skill_repo.get(skill.id)
        assert fetched.is_active is False
        assert fetched.status == "inactive"

    def test_deactivate_skill_nonexistent(self, skill_repo):
        """deactivate() on a non-existent ID returns None."""
        assert skill_repo.deactivate("ghost-id") is None


# =============================================================================
# SkillRepository — COUNTERS
# =============================================================================


class TestIncrementCounter:
    """Tests for :meth:`SkillRepository.increment_counter`."""

    def test_increment_counter(self, skill_repo, project_id):
        """Incrementing a counter adds to the existing value."""
        skill = _make_skill(skill_repo, project_id, "foxtrot")

        skill_repo.increment_counter(skill.id, "total_selections")
        fetched = skill_repo.get(skill.id)
        assert fetched.total_selections == 1

        skill_repo.increment_counter(skill.id, "total_selections", amount=5)
        fetched = skill_repo.get(skill.id)
        assert fetched.total_selections == 6

    def test_increment_counter_unknown_column_raises(self, skill_repo, project_id):
        """An unknown column name raises ``ValueError``."""
        skill = _make_skill(skill_repo, project_id, "foxtrot")
        with pytest.raises(ValueError) as exc_info:
            skill_repo.increment_counter(skill.id, "no_such_counter")
        msg = str(exc_info.value)
        assert "no_such_counter" in msg
        assert "Unknown" in msg or "Allowed" in msg

    def test_increment_counter_negative(self, skill_repo, project_id):
        """Negative amounts decrement (used to reset consecutive_failures)."""
        skill = _make_skill(skill_repo, project_id, "foxtrot")

        skill_repo.increment_counter(skill.id, "consecutive_failures", amount=4)
        skill_repo.increment_counter(skill.id, "consecutive_failures", amount=-4)
        fetched = skill_repo.get(skill.id)
        assert fetched.consecutive_failures == 0


# =============================================================================
# SkillRepository — A/B VARIANTS
# =============================================================================


class TestABVariants:
    """Tests for A/B variant queries on :class:`SkillRepository`."""

    def test_get_ab_variants(self, skill_repo, project_id):
        """get_ab_variants returns all skills sharing an ``ab_test_group``."""
        group = "ab-group-1"
        _make_skill(skill_repo, project_id, "old", ab_test_group=group)
        _make_skill(skill_repo, project_id, "new", ab_test_group=group)
        # An unrelated skill in a different group.
        _make_skill(skill_repo, project_id, "other", ab_test_group="other-group")

        variants = skill_repo.get_ab_variants(group)
        names = {s.name for s in variants}
        assert names == {"old", "new"}
        assert len(variants) == 2

    def test_get_active_variant(self, skill_repo, project_id):
        """get_active_variant returns the active row when only one is active."""
        old_skill = _make_skill(skill_repo, project_id, "evolved", generation=0)
        new_skill = _make_skill(skill_repo, project_id, "evolved", generation=1)
        # Deactivate the older generation.
        skill_repo.deactivate(old_skill.id)

        active = skill_repo.get_active_variant(project_id, "evolved")
        assert active is not None
        assert active.id == new_skill.id
        assert active.is_active is True

    def test_get_active_variant_returns_none_when_all_inactive(
        self, skill_repo, project_id
    ):
        """get_active_variant returns None when no active row exists."""
        s = _make_skill(skill_repo, project_id, "x")
        skill_repo.deactivate(s.id)
        assert skill_repo.get_active_variant(project_id, "x") is None


# =============================================================================
# SkillRepository — BM25 SEARCH
# =============================================================================


class TestSearchBM25:
    """Tests for :meth:`SkillRepository.search_bm25`."""

    def test_search_bm25(self, skill_repo, project_id):
        """A relevant query returns the matching skill."""
        _make_skill(
            skill_repo,
            project_id,
            "database-migration",
            description="How to migrate a postgres database",
            content="Run pg_dump and restore with psql.",
        )
        _make_skill(
            skill_repo,
            project_id,
            "frontend-routing",
            description="Set up client-side routing in React",
            content="Use react-router-dom v6.",
        )

        results = skill_repo.search_bm25(project_id, "database migration postgres")

        assert len(results) >= 1
        assert results[0].name == "database-migration"

    def test_search_bm25_empty_for_stopword_only_query(self, skill_repo, project_id):
        """A query containing only stopwords returns no results."""
        _make_skill(skill_repo, project_id, "anything")
        assert skill_repo.search_bm25(project_id, "the and is") == []

    def test_search_bm25_respects_limit(self, skill_repo, project_id):
        """``limit`` caps the number of returned results."""
        for i in range(5):
            _make_skill(
                skill_repo,
                project_id,
                f"skill-{i}",
                description=f"shared keyword {i}",
            )

        results = skill_repo.search_bm25(project_id, "shared keyword", limit=3)
        assert len(results) <= 3

    def test_search_bm25_includes_global_skills(self, skill_repo, project_id):
        """Global skills (``project_id IS NULL``) are also matched."""
        # Project-scoped skill.
        _make_skill(
            skill_repo,
            project_id,
            "proj-deploy",
            description="deploy to production",
        )
        # Global skill (no project_id).
        _make_skill(
            skill_repo,
            None,
            "global-rollback",
            description="how to rollback a deployment safely",
        )

        # A search from the project should include both project AND
        # global skills that match.
        results = skill_repo.search_bm25(project_id, "deployment")
        names = {s.name for s in results}
        assert "global-rollback" in names


# =============================================================================
# SkillLineageRepository
# =============================================================================


class TestSkillLineage:
    """Tests for :class:`SkillLineageRepository`."""

    def test_create_lineage(self, lineage_repo, skill_repo, project_id):
        """create() inserts a parent/child edge."""
        parent = _make_skill(skill_repo, project_id, "p")
        child = _make_skill(
            skill_repo, project_id, "c", generation=1
        )

        edge = lineage_repo.create(
            skill_id=child.id,
            parent_skill_id=parent.id,
            change_summary="tweaked fallback",
            content_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        )

        assert edge.skill_id == child.id
        assert edge.parent_skill_id == parent.id
        assert edge.change_summary == "tweaked fallback"
        assert "+new" in edge.content_diff
        assert edge.created_at is not None

    def test_get_parents(self, lineage_repo, skill_repo, project_id):
        """get_parents returns the edges where ``skill_id`` is the descendant."""
        p1 = _make_skill(skill_repo, project_id, "p1")
        p2 = _make_skill(skill_repo, project_id, "p2")
        child = _make_skill(
            skill_repo, project_id, "c", generation=2
        )
        lineage_repo.create(skill_id=child.id, parent_skill_id=p1.id)
        lineage_repo.create(skill_id=child.id, parent_skill_id=p2.id)

        parents = lineage_repo.get_parents(child.id)
        assert len(parents) == 2
        parent_ids = {edge.parent_skill_id for edge in parents}
        assert parent_ids == {p1.id, p2.id}

    def test_get_parents_empty_for_root(self, lineage_repo, skill_repo, project_id):
        """A root-imported skill has no parents."""
        root = _make_skill(skill_repo, project_id, "root")
        assert lineage_repo.get_parents(root.id) == []

    def test_get_children(self, lineage_repo, skill_repo, project_id):
        """get_children returns the edges where ``parent_skill_id`` is the ancestor."""
        parent = _make_skill(skill_repo, project_id, "p")
        c1 = _make_skill(skill_repo, project_id, "c1", generation=1)
        c2 = _make_skill(skill_repo, project_id, "c2", generation=1)
        lineage_repo.create(skill_id=c1.id, parent_skill_id=parent.id)
        lineage_repo.create(skill_id=c2.id, parent_skill_id=parent.id)

        children = lineage_repo.get_children(parent.id)
        assert len(children) == 2
        child_ids = {edge.skill_id for edge in children}
        assert child_ids == {c1.id, c2.id}

    def test_get_children_empty_for_leaf(self, lineage_repo, skill_repo, project_id):
        """A leaf skill (not yet evolved) has no children."""
        leaf = _make_skill(skill_repo, project_id, "leaf")
        assert lineage_repo.get_children(leaf.id) == []


# =============================================================================
# SkillUsageRepository
# =============================================================================


class TestSkillUsage:
    """Tests for :class:`SkillUsageRepository`."""

    def test_create_usage_record(self, usage_repo, skill_repo, project_id):
        """create() inserts a usage record with the right fields."""
        skill = _make_skill(skill_repo, project_id, "u")
        record = usage_repo.create(
            skill_id=skill.id,
            project_id=project_id,
            instance_id="inst-1",
            agent_id="agent-x",
            task_message="do the thing",
            selected=True,
            applied=True,
            task_succeeded=True,
            iterations=3,
            duration_seconds=42,
            fallback=False,
        )

        assert record.id is not None
        assert record.skill_id == skill.id
        assert record.project_id == project_id
        assert record.instance_id == "inst-1"
        assert record.agent_id == "agent-x"
        assert record.task_message == "do the thing"
        assert record.selected is True
        assert record.applied is True
        assert record.task_succeeded is True
        assert record.iterations == 3
        assert record.duration_seconds == 42
        assert record.fallback is False
        # Feedback fields start unset.
        assert record.feedback_applied is None
        assert record.feedback_note is None

    def test_get_by_skill(self, usage_repo, skill_repo, project_id):
        """get_by_skill returns usage records with pagination."""
        skill_a = _make_skill(skill_repo, project_id, "a")
        skill_b = _make_skill(skill_repo, project_id, "b")
        for _ in range(3):
            usage_repo.create(
                skill_id=skill_a.id,
                project_id=project_id,
                instance_id="i",
                agent_id="a",
            )
        usage_repo.create(
            skill_id=skill_b.id,
            project_id=project_id,
            instance_id="i",
            agent_id="a",
        )

        items, total = usage_repo.get_by_skill(skill_a.id)
        assert total == 3
        assert len(items) == 3

        page1, total1 = usage_repo.get_by_skill(
            skill_a.id, limit=2, offset=0
        )
        page2, total2 = usage_repo.get_by_skill(
            skill_a.id, limit=2, offset=2
        )
        assert total1 == 3
        assert total2 == 3
        assert len(page1) == 2
        assert len(page2) == 1

    def test_get_stats(self, usage_repo, skill_repo, project_id):
        """get_stats computes per-signal counts and rates."""
        skill = _make_skill(skill_repo, project_id, "stats")

        # Build a mix of outcomes:
        # 4 total, 3 selected, 2 applied, 2 succeeded, 1 fallback.
        usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="i", agent_id="a",
            selected=True, applied=True, task_succeeded=True,
        )
        usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="i", agent_id="a",
            selected=True, applied=True, task_succeeded=True,
            fallback=True,
        )
        usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="i", agent_id="a",
            selected=True, applied=False, task_succeeded=False,
        )
        usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="i", agent_id="a",
            selected=False, applied=False, task_succeeded=False,
        )

        stats = usage_repo.get_stats(skill.id)

        assert stats["total"] == 4
        assert stats["selected"] == 3
        assert stats["applied"] == 2
        assert stats["completions"] == 2
        assert stats["fallbacks"] == 1
        assert stats["completion_rate"] == 0.5
        assert stats["fallback_rate"] == 0.25

    def test_get_stats_empty(self, usage_repo, skill_repo, project_id):
        """get_stats on a skill with no records returns zero-filled dict."""
        skill = _make_skill(skill_repo, project_id, "empty")
        stats = usage_repo.get_stats(skill.id)
        assert stats == {
            "total": 0,
            "selected": 0,
            "applied": 0,
            "completions": 0,
            "fallbacks": 0,
            "completion_rate": 0.0,
            "fallback_rate": 0.0,
        }

    def test_update_feedback(self, usage_repo, skill_repo, project_id):
        """update_feedback stamps applied+note onto a record."""
        skill = _make_skill(skill_repo, project_id, "f")
        record = usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="i", agent_id="a",
        )

        updated = usage_repo.update_feedback(
            record.id, applied=True, note="helped user fix typo"
        )
        assert updated is not None
        assert updated.feedback_applied is True
        assert updated.feedback_note == "helped user fix typo"

        # And re-fetching shows the same.
        items, _ = usage_repo.get_by_skill(skill.id)
        assert items[0].feedback_applied is True
        assert items[0].feedback_note == "helped user fix typo"

    def test_update_feedback_nonexistent_returns_none(self, usage_repo):
        """update_feedback on an unknown record returns None."""
        assert usage_repo.update_feedback("ghost", True, "note") is None

    def test_count_comparisons(
        self, usage_repo, skill_repo, ab_test_repo, project_id
    ):
        """count_comparisons returns per-skill record counts for a group."""
        s_old = _make_skill(skill_repo, project_id, "old")
        s_new = _make_skill(skill_repo, project_id, "new")
        group = "ab-cmp"
        # Persist the ``ab_test_group`` assignment on both skills via
        # the repository so the count_comparisons query can find
        # them. Setting the attribute on the Python object alone has
        # no effect — the change must round-trip through the DB.
        skill_repo.update(s_old.id, ab_test_group=group)
        skill_repo.update(s_new.id, ab_test_group=group)

        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )

        # 3 records for old, 5 for new.
        for _ in range(3):
            usage_repo.create(
                skill_id=s_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
            )
        for _ in range(5):
            usage_repo.create(
                skill_id=s_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
            )

        counts = usage_repo.count_comparisons(group)
        assert counts == {s_old.id: 3, s_new.id: 5}

    def test_count_comparisons_empty_group(
        self, usage_repo, skill_repo, ab_test_repo, project_id
    ):
        """count_comparisons for an unknown group returns an empty dict."""
        # A skill not in any A/B test group.
        _make_skill(skill_repo, project_id, "loner")
        assert usage_repo.count_comparisons("never-existed") == {}

    def test_get_applied_for_instance(self, usage_repo, skill_repo, project_id):
        """get_applied_for_instance returns rows with feedback_applied=True."""
        skill = _make_skill(skill_repo, project_id, "ai")
        rec_a = usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="inst-A", agent_id="a",
        )
        rec_b = usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="inst-A", agent_id="a",
        )
        rec_other = usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="inst-OTHER", agent_id="a",
        )

        # Apply feedback to A's two records; leave the other untouched.
        usage_repo.update_feedback(rec_a.id, True, "yep")
        usage_repo.update_feedback(rec_b.id, False, "nope but recorded")
        # rec_other is on a different instance, so it should not be returned
        # regardless of feedback status.

        applied = usage_repo.get_applied_for_instance("inst-A")
        # ``applied`` here means ``feedback_applied=True`` (the method's
        # contract, not the skill's "applied" boolean).
        assert {r.id for r in applied} == {rec_a.id}
        assert all(r.feedback_applied is True for r in applied)

        # Sanity: other instance shows up empty even with feedback
        # applied (it's filtered out by instance_id).
        assert usage_repo.get_applied_for_instance("inst-OTHER") == []

    def test_has_applied_for_instance_true(self, usage_repo, skill_repo, project_id):
        """has_applied_for_instance returns True when at least one record exists."""
        skill = _make_skill(skill_repo, project_id, "h")
        rec = usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="inst-X", agent_id="a",
        )
        usage_repo.update_feedback(rec.id, True, "ok")

        assert usage_repo.has_applied_for_instance("inst-X") is True

    def test_has_applied_for_instance_false_when_no_records(
        self, usage_repo, skill_repo, project_id
    ):
        """has_applied_for_instance returns False for an instance with no records."""
        skill = _make_skill(skill_repo, project_id, "h")
        rec = usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id="inst-NOTHING", agent_id="a",
        )
        # No feedback applied.
        assert usage_repo.has_applied_for_instance("inst-NOTHING") is False
        # Sanity: feedback was truly not applied.
        items, _ = usage_repo.get_by_skill(skill.id)
        assert items[0].feedback_applied is None


# =============================================================================
# SkillTriggerRepository
# =============================================================================


class TestSkillTrigger:
    """Tests for :class:`SkillTriggerRepository`."""

    def test_create_trigger(self, trigger_repo, project_id):
        """create() inserts a trigger rule."""
        trigger = trigger_repo.create(
            name="deploy-trigger",
            condition_type="keyword",
            condition_json={"keyword": "deploy"},
            action="select_skill:deploy",
            project_id=project_id,
        )
        assert trigger.id is not None
        assert trigger.name == "deploy-trigger"
        assert trigger.condition_type == "keyword"
        assert trigger.condition_json == {"keyword": "deploy"}
        assert trigger.action == "select_skill:deploy"
        assert trigger.project_id == project_id
        assert trigger.is_enabled is True

    def test_get_trigger(self, trigger_repo, project_id):
        """get() returns the row matching the ID."""
        created = trigger_repo.create(
            name="t",
            condition_type="regex",
            condition_json={"pattern": "^run\\s+tests?$"},
            action="select_skill:test-runner",
            project_id=project_id,
        )
        fetched = trigger_repo.get(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.condition_json == {"pattern": "^run\\s+tests?$"}

    def test_get_trigger_nonexistent(self, trigger_repo):
        """get() on a non-existent ID returns None."""
        assert trigger_repo.get("ghost") is None

    def test_list_triggers_enabled_only(self, trigger_repo):
        """``list(enabled_only=True)`` filters out disabled rows.

        The current ``SkillTriggerRepository.list`` only returns
        rows where ``project_id IS NULL`` (the docstring describes
        a future per-project filter — see the next test). Both
        triggers here are global, so the filter applies cleanly.
        """
        enabled = trigger_repo.create(
            name="en",
            condition_type="keyword",
            condition_json={"k": "v"},
            action="a",
            project_id=None,
        )
        disabled = trigger_repo.create(
            name="dis",
            condition_type="keyword",
            condition_json={"k": "v"},
            action="a",
            project_id=None,
        )
        trigger_repo.update(disabled.id, is_enabled=False)

        # enabled_only=True (default): only the enabled one.
        listed_enabled = trigger_repo.list(
            project_id=None, enabled_only=True
        )
        listed_enabled_ids = {t.id for t in listed_enabled}
        assert listed_enabled_ids == {enabled.id}

        # enabled_only=False: both rows.
        listed_all = trigger_repo.list(
            project_id=None, enabled_only=False
        )
        listed_all_ids = {t.id for t in listed_all}
        assert listed_all_ids == {enabled.id, disabled.id}

    def test_list_triggers_project_filter(
        self, trigger_repo, project_id, other_project_id
    ):
        """list() returns only rows where ``project_id IS NULL``.

        Note: the current implementation always filters by
        ``project_id IS NULL`` regardless of the ``project_id`` arg —
        the docstring describes a future behavior. This test pins the
        current behavior so the change is intentional and reviewable.
        """
        # Global triggers (project_id IS NULL).
        g1 = trigger_repo.create(
            name="g1",
            condition_type="keyword",
            condition_json={"k": "v"},
            action="a",
            project_id=None,
        )
        g2 = trigger_repo.create(
            name="g2",
            condition_type="regex",
            condition_json={"pattern": "^x$"},
            action="a",
            project_id=None,
        )

        # Project-scoped triggers (should NOT be returned).
        trigger_repo.create(
            name="p1",
            condition_type="keyword",
            condition_json={"k": "v"},
            action="a",
            project_id=project_id,
        )
        trigger_repo.create(
            name="p2",
            condition_type="keyword",
            condition_json={"k": "v"},
            action="a",
            project_id=other_project_id,
        )

        listed = trigger_repo.list(project_id=project_id, enabled_only=False)
        listed_ids = {t.id for t in listed}
        # Only the global ones are returned, regardless of the
        # ``project_id`` arg passed in.
        assert listed_ids == {g1.id, g2.id}

    def test_update_trigger(self, trigger_repo, project_id):
        """update() changes trigger fields."""
        created = trigger_repo.create(
            name="t",
            condition_type="keyword",
            condition_json={"k": "v"},
            action="a",
            project_id=project_id,
        )

        updated = trigger_repo.update(
            created.id,
            action="select_skill:new-action",
            is_enabled=False,
        )
        assert updated is not None
        assert updated.action == "select_skill:new-action"
        assert updated.is_enabled is False

    def test_update_trigger_unknown_field_raises(self, trigger_repo, project_id):
        """update() with a non-existent field raises AttributeError."""
        created = trigger_repo.create(
            name="t",
            condition_type="keyword",
            condition_json={"k": "v"},
            action="a",
            project_id=project_id,
        )
        with pytest.raises(AttributeError):
            trigger_repo.update(created.id, no_such_field="x")

    def test_update_trigger_nonexistent_returns_none(self, trigger_repo):
        """update() on a non-existent ID returns None."""
        assert trigger_repo.update("ghost", name="x") is None

    def test_delete_trigger(self, trigger_repo, project_id):
        """delete() removes the row."""
        created = trigger_repo.create(
            name="t",
            condition_type="keyword",
            condition_json={"k": "v"},
            action="a",
            project_id=project_id,
        )
        assert trigger_repo.delete(created.id) is True
        assert trigger_repo.get(created.id) is None

    def test_delete_trigger_nonexistent(self, trigger_repo):
        """delete() on a non-existent ID returns False."""
        assert trigger_repo.delete("ghost") is False


# =============================================================================
# SkillEmbeddingRepository
# =============================================================================


class TestSkillEmbedding:
    """Tests for :class:`SkillEmbeddingRepository`."""

    def test_create_embedding(self, embedding_repo, skill_repo, project_id):
        """create() stores a list-of-floats embedding."""
        skill = _make_skill(skill_repo, project_id, "e")
        vec = [0.1, 0.2, 0.3, 0.4]

        row = embedding_repo.create(
            skill_id=skill.id,
            trigger_query="how to deploy",
            embedding=vec,
        )

        assert row.id is not None
        assert row.skill_id == skill.id
        assert row.trigger_query == "how to deploy"
        assert list(row.embedding) == vec
        assert row.created_at is not None

    def test_get_by_skill(self, embedding_repo, skill_repo, project_id):
        """get_by_skill returns every embedding for a skill."""
        skill_a = _make_skill(skill_repo, project_id, "a")
        skill_b = _make_skill(skill_repo, project_id, "b")
        embedding_repo.create(
            skill_id=skill_a.id, trigger_query="q1", embedding=[1.0, 0.0]
        )
        embedding_repo.create(
            skill_id=skill_a.id, trigger_query="q2", embedding=[0.0, 1.0]
        )
        embedding_repo.create(
            skill_id=skill_b.id, trigger_query="q3", embedding=[0.5, 0.5]
        )

        a_embs = embedding_repo.get_by_skill(skill_a.id)
        b_embs = embedding_repo.get_by_skill(skill_b.id)

        assert len(a_embs) == 2
        assert {e.trigger_query for e in a_embs} == {"q1", "q2"}
        assert len(b_embs) == 1
        assert b_embs[0].trigger_query == "q3"

    def test_get_by_skill_empty(self, embedding_repo, skill_repo, project_id):
        """get_by_skill for a skill with no embeddings returns []."""
        skill = _make_skill(skill_repo, project_id, "lonely")
        assert embedding_repo.get_by_skill(skill.id) == []

    def test_delete_by_skill(self, embedding_repo, skill_repo, project_id):
        """delete_by_skill removes all embeddings for a skill and returns the count."""
        skill = _make_skill(skill_repo, project_id, "d")
        for i in range(3):
            embedding_repo.create(
                skill_id=skill.id, trigger_query=f"q{i}", embedding=[0.0]
            )

        deleted = embedding_repo.delete_by_skill(skill.id)
        assert deleted == 3
        assert embedding_repo.get_by_skill(skill.id) == []

    def test_delete_by_skill_no_rows(self, embedding_repo, skill_repo, project_id):
        """delete_by_skill for a skill with no embeddings returns 0."""
        skill = _make_skill(skill_repo, project_id, "no-emb")
        assert embedding_repo.delete_by_skill(skill.id) == 0

    def test_get_all_for_project_filters_by_project(
        self, embedding_repo, skill_repo, project_id, other_project_id
    ):
        """get_all_for_project filters embeddings to the requested project."""
        skill_p = _make_skill(skill_repo, project_id, "p")
        skill_o = _make_skill(skill_repo, other_project_id, "o")

        embedding_repo.create(
            skill_id=skill_p.id, trigger_query="p1", embedding=[0.1]
        )
        embedding_repo.create(
            skill_id=skill_p.id, trigger_query="p2", embedding=[0.2]
        )
        embedding_repo.create(
            skill_id=skill_o.id, trigger_query="o1", embedding=[0.3]
        )

        results = embedding_repo.get_all_for_project(project_id)
        skill_ids = {sid for _, sid in results}
        assert skill_ids == {skill_p.id}

        other_results = embedding_repo.get_all_for_project(other_project_id)
        other_skill_ids = {sid for _, sid in other_results}
        assert other_skill_ids == {skill_o.id}

    def test_get_all_for_project_excludes_global_skills(
        self, embedding_repo, skill_repo, project_id
    ):
        """get_all_for_project excludes global skills unless project_id=None."""
        skill_proj = _make_skill(skill_repo, project_id, "proj-skill")
        skill_global = _make_skill(skill_repo, None, "global-skill")

        embedding_repo.create(
            skill_id=skill_proj.id, trigger_query="p", embedding=[0.0]
        )
        embedding_repo.create(
            skill_id=skill_global.id, trigger_query="g", embedding=[0.0]
        )

        # Project filter excludes the global skill.
        results = embedding_repo.get_all_for_project(project_id)
        skill_ids = {sid for _, sid in results}
        assert skill_proj.id in skill_ids
        assert skill_global.id not in skill_ids

        # ``project_id=None`` returns all embeddings across all skills
        # (including global).
        all_results = embedding_repo.get_all_for_project(None)
        all_skill_ids = {sid for _, sid in all_results}
        assert skill_proj.id in all_skill_ids
        assert skill_global.id in all_skill_ids


# =============================================================================
# SkillABTestRepository
# =============================================================================


class TestSkillABTest:
    """Tests for :class:`SkillABTestRepository`."""

    def test_create_ab_test(
        self, ab_test_repo, skill_repo, project_id
    ):
        """create_ab_test() registers a new test group."""
        s_old = _make_skill(skill_repo, project_id, "old")
        s_new = _make_skill(skill_repo, project_id, "new")
        group = "ab-test-1"

        test = ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )

        assert test.id is not None
        assert test.ab_test_group == group
        assert test.skill_id_old == s_old.id
        assert test.skill_id_new == s_new.id
        # Defaults: counters zero, unresolved.
        assert test.extension_count == 0
        assert test.comparisons == 0
        assert test.created_at is not None
        assert test.resolved_at is None
        assert test.winner_skill_id is None

    def test_get_by_group(self, ab_test_repo, skill_repo, project_id):
        """get_by_group returns the row matching the group."""
        s_old = _make_skill(skill_repo, project_id, "o")
        s_new = _make_skill(skill_repo, project_id, "n")
        group = "ab-grp"
        created = ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )

        fetched = ab_test_repo.get_by_group(group)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.ab_test_group == group

    def test_get_by_group_nonexistent(self, ab_test_repo):
        """get_by_group for an unknown group returns None."""
        assert ab_test_repo.get_by_group("never-existed") is None

    def test_increment_comparison(self, ab_test_repo, skill_repo, project_id):
        """increment_comparison atomically bumps the ``comparisons`` counter."""
        s_old = _make_skill(skill_repo, project_id, "o")
        s_new = _make_skill(skill_repo, project_id, "n")
        group = "ab-cmp"
        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )

        ab_test_repo.increment_comparison(group)
        ab_test_repo.increment_comparison(group)
        ab_test_repo.increment_comparison(group)

        fetched = ab_test_repo.get_by_group(group)
        assert fetched.comparisons == 3
        # Sanity: extension_count untouched.
        assert fetched.extension_count == 0

    def test_increment_extension(self, ab_test_repo, skill_repo, project_id):
        """increment_extension atomically bumps ``extension_count``."""
        s_old = _make_skill(skill_repo, project_id, "o")
        s_new = _make_skill(skill_repo, project_id, "n")
        group = "ab-ext"
        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )

        ab_test_repo.increment_extension(group)
        ab_test_repo.increment_extension(group)

        fetched = ab_test_repo.get_by_group(group)
        assert fetched.extension_count == 2
        # Sanity: comparisons untouched.
        assert fetched.comparisons == 0

    def test_resolve(self, ab_test_repo, skill_repo, project_id):
        """resolve() sets ``resolved_at`` and ``winner_skill_id``."""
        s_old = _make_skill(skill_repo, project_id, "o")
        s_new = _make_skill(skill_repo, project_id, "n")
        group = "ab-resolve"
        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )

        resolved = ab_test_repo.resolve(
            ab_test_group=group, winner_skill_id=s_new.id
        )
        assert resolved is not None
        assert resolved.resolved_at is not None
        assert resolved.winner_skill_id == s_new.id

        # And the change is visible via get_by_group.
        fetched = ab_test_repo.get_by_group(group)
        assert fetched.resolved_at is not None
        assert fetched.winner_skill_id == s_new.id

    def test_resolve_nonexistent_returns_none(self, ab_test_repo):
        """resolve() on an unknown group returns None."""
        assert ab_test_repo.resolve("never-existed", "winner-id") is None

    def test_get_active_tests(self, ab_test_repo, skill_repo, project_id):
        """get_active_tests returns only unresolved rows."""
        s_old = _make_skill(skill_repo, project_id, "o")
        s_new = _make_skill(skill_repo, project_id, "n")
        s_old2 = _make_skill(skill_repo, project_id, "o2")
        s_new2 = _make_skill(skill_repo, project_id, "n2")

        group_active = "ab-active"
        group_resolved = "ab-resolved"
        ab_test_repo.create_ab_test(
            ab_test_group=group_active,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )
        ab_test_repo.create_ab_test(
            ab_test_group=group_resolved,
            skill_id_old=s_old2.id,
            skill_id_new=s_new2.id,
        )
        ab_test_repo.resolve(group_resolved, winner_skill_id=s_new2.id)

        active = ab_test_repo.get_active_tests()
        active_groups = {t.ab_test_group for t in active}
        assert active_groups == {group_active}