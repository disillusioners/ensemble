"""F7 pins — feedback BY NAME (skill-evolution R1).

THE GAP THIS CLOSES

``SkillMetricsService.record_feedback`` was UUID-only on BOTH branches
(the usage lookup filters ``SkillUsageRecord.skill_id == skill_id``;
the row lookup is ``skill_repo.get(skill_id)``). A bare skill NAME
resolved to NEITHER, so name-keyed feedback was DROPPED ENTIRELY —
measured: 13,412 usage rows, ALL uuid-keyed, 0 non-uuid; 6 payloads
dropped in the last 24h (unrecoverable — NO backfill is attempted).

Also closed here:

* the auto-load block RENDERED NO ID AT ALL — the ids rode only in
  ``additional_kwargs["auto_load_skill_ids"]`` (machine-side dedup),
  never in the text an agent reads;
* the ``skill_feedback`` tool's documented input form did not accept a
  bare NAME.

NAMED MUTATION LEGS (each turns this file RED):

* **M-F7a** — drop the name resolution from ``record_feedback`` (revert
  to UUID-only) → the name→uuid test REDs.
* **M-F7b** — write the NAME (not the resolved uuid) into the usage row
  → the resolved-uuid-recorded test REDs.
* **M-F7c** — break the ``pf:`` lane dispatch → the pf-lane test REDs.
* **M-F7d** — drop the inline id from the auto-load text → the
  auto-load-text test REDs.
* **M-F7e** — remove NAME from the documented input form → the doc test
  REDs.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.skill.models import (  # noqa: F401 — tables
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from daemon.repositories.skill.repository import SkillRepository

PROJECT = "p-f7-feedback"


# ── Harness ───────────────────────────────────────────────────────────────


class _FakeConfig:
    ab_sample_size = 10
    ab_min_difference = 0.15
    max_extensions = 3


class _FakeInstanceRepo:
    def get(self, instance_id):
        return None


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'f7-feedback.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def skill_repo(engine):
    return SkillRepository(engine)


@pytest.fixture
def metrics_service(engine, skill_repo):
    from daemon.repositories.skill.repository import (
        SkillABTestRepository,
        SkillTriggerRepository,
        SkillUsageRepository,
    )
    from daemon.services.skill_metrics_service import SkillMetricsService

    return SkillMetricsService(
        usage_repo=SkillUsageRepository(engine),
        skill_repo=skill_repo,
        trigger_repo=SkillTriggerRepository(engine),
        ab_test_repo=SkillABTestRepository(engine),
        config=_FakeConfig(),
        instance_repo=_FakeInstanceRepo(),
    )


def _make_skill(skill_repo, name="f7-skill", generation=0):
    return skill_repo.create(
        name=name,
        description="d",
        content="c",
        project_id=PROJECT,
        generation=generation,
    )


# ── name → uuid resolution at the SERVICE ────────────────────────────────


class TestFeedbackByName:
    @pytest.mark.asyncio
    async def test_name_resolves_and_is_recorded_as_the_UUID(
        self, metrics_service, skill_repo
    ):
        """A bare NAME lands feedback on the right skill, and the usage
        row carries the RESOLVED uuid — never the name."""
        skill = _make_skill(skill_repo, name="name-keyed-skill")
        inst = "inst-f7-name"
        metrics_service.usage_repo.create(
            skill_id=skill.id, project_id=PROJECT,
            instance_id=inst, agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id="name-keyed-skill",  # ← the NAME, not the uuid
            instance_id=inst,
            agent_id="a",
            project_id=PROJECT,
            applied=True,
            note="by name",
        )

        assert ok is True
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst
        )
        assert rec is not None
        assert rec.skill_id == skill.id, (
            "the resolved UUID must be recorded — never the name"
        )
        assert rec.feedback_note == "by name"
        # The counter bump targets the resolved row.
        assert skill_repo.get(skill.id).total_applied == 1

    @pytest.mark.asyncio
    async def test_on_miss_insert_uses_the_resolved_uuid(
        self, metrics_service, skill_repo
    ):
        """The on-miss insert path records the RESOLVED uuid too."""
        skill = _make_skill(skill_repo, name="on-miss-name")
        inst = "inst-f7-onmiss"

        ok = await metrics_service.record_feedback(
            skill_id="on-miss-name",
            instance_id=inst,
            agent_id="a",
            project_id=PROJECT,
            applied=None,
            note="first contact",
        )

        assert ok is True
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst
        )
        assert rec is not None
        assert rec.skill_id == skill.id

    @pytest.mark.asyncio
    async def test_uuid_path_is_unchanged(self, metrics_service, skill_repo):
        skill = _make_skill(skill_repo, name="uuid-path")
        inst = "inst-f7-uuid"
        metrics_service.usage_repo.create(
            skill_id=skill.id, project_id=PROJECT,
            instance_id=inst, agent_id="a",
        )
        ok = await metrics_service.record_feedback(
            skill_id=skill.id,  # uuid form
            instance_id=inst,
            agent_id="a",
            project_id=PROJECT,
            applied=True,
            note="uuid",
        )
        assert ok is True
        assert skill_repo.get(skill.id).total_applied == 1

    @pytest.mark.asyncio
    async def test_unknown_name_still_soft_fails(
        self, metrics_service, skill_repo
    ):
        ok = await metrics_service.record_feedback(
            skill_id="no-such-skill-anywhere",
            instance_id="inst-x",
            agent_id="a",
            project_id=PROJECT,
            applied=True,
            note="",
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_name_resolution_is_project_scoped(
        self, metrics_service, skill_repo
    ):
        """The same name in ANOTHER project does not resolve here."""
        other = skill_repo.create(
            name="shared-name", description="d", content="c",
            project_id="some-other-project",
        )
        ok = await metrics_service.record_feedback(
            skill_id="shared-name",
            instance_id="inst-y",
            agent_id="a",
            project_id=PROJECT,   # ← different project
            applied=True,
            note="",
        )
        assert ok is False
        # The other project's row was NOT touched.
        assert skill_repo.get(other.id).total_applied == 0


# ── The pf: lane is untouched ────────────────────────────────────────────


class TestPfLaneUnaffected:
    @pytest.mark.asyncio
    async def test_pf_addresses_still_route_to_the_companion_path(self):
        """``pf:`` / ``pf-`` ids still dispatch to the companion path —
        the name-resolution change never intercepts them.

        Driven end-to-end: with NO companion source wired the pf lane's
        own soft-fail answer comes back and the DB metrics service is
        never called (that soft-fail IS the companion lane's fingerprint
        — a name-resolution leak would instead call
        ``record_feedback``).
        """
        from daemon.tools.skill_tools import create_skill_tools

        manager = MagicMock()
        manager._skill_metrics_service = MagicMock()
        metrics = manager._skill_metrics_service
        metrics.record_feedback = MagicMock()
        # The companion (file-skill) lane is absent → its own soft fail.
        manager._project_skill_source = None
        manager._project_skill_file_repository = None
        manager._project_skill_usage_repository = None

        tools = {t.name: t for t in create_skill_tools(manager, "inst-f7")}

        out = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:some-skill", "applied": True}
        )

        assert "File-skill feedback" in out, (
            f"the pf lane must own the outcome, got {out!r}"
        )
        metrics.record_feedback.assert_not_called()


# ── The auto-load TEXT now carries the id ────────────────────────────────


class TestAutoLoadRendersTheId:
    def test_auto_load_text_contains_id_and_name(self):
        from daemon.services.context_messages import (
            build_auto_load_skills_message,
        )

        msg = build_auto_load_skills_message(
            body="\U0001f4cb **Skill: dev-strategy** (id: abc-123)\n\nbody",
            skill_ids=["abc-123"],
            instance_id="inst-1",
            agent_id="developer",
        )
        assert msg is not None
        assert "abc-123" in msg.content
        assert "dev-strategy" in msg.content
        # The machine-side channel is still present (no regression).
        assert msg.additional_kwargs["auto_load_skill_ids"] == ["abc-123"]

    def test_build_block_renders_header_from_skill_rows(self):
        """The real builder path renders ``📋 **Skill: <name>** (id:
        <uuid>)`` ahead of every auto-loaded skill body."""
        from unittest.mock import patch

        from daemon.services import context_messages

        skill = MagicMock()
        skill.id = "uuid-auto-1"
        skill.name = "auto-skill"
        skill.content = "# Body\nsteps"

        manager = MagicMock()
        repo = MagicMock()
        agent_meta = MagicMock()
        agent_meta.id = "developer"

        async def _fake_fetch(**kwargs):
            return ([skill], ["uuid-auto-1"])

        with patch.object(
            context_messages, "_fetch_auto_load_skills", side_effect=_fake_fetch
        ):
            msg = asyncio.run(
                context_messages._build_auto_load_block(
                    agent_meta=agent_meta,
                    instance_id="inst-2",
                    project_id=PROJECT,
                    manager=manager,
                    instance_repository=repo,
                )
            )

        assert msg is not None
        assert "uuid-auto-1" in msg.content, (
            "the auto-load TEXT must render the skill id — an agent can "
            f"only give feedback on what it can name: {msg.content!r}"
        )
        assert "auto-skill" in msg.content
        assert "# Body" in msg.content


# ── The documented input form accepts a NAME ─────────────────────────────


class TestDocumentedInputForm:
    def test_the_tool_docs_accept_a_bare_name(self):
        from daemon.tools.skill_tools import create_skill_tools

        tools = {
            t.name: t for t in create_skill_tools(MagicMock(), "inst-f7")
        }
        doc = tools["skill_feedback"]._full_doc_
        assert "NAME" in doc, (
            "the documented input form must present the bare-name form"
        )
        assert "get_by_name" in doc
        # The tool function's own docstring (the OTHER documented
        # surface the spec names) presents the bare-name form too.
        import inspect

        import daemon.tools.skill_tools as st

        src = inspect.getsource(st.create_skill_tools)
        assert "a bare skill NAME" in src
