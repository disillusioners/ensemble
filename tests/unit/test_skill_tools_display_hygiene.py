"""E3 — skill-tools DISPLAY HYGIENE (board `wi-d340f16c`, plan §9 E3 / §18 rows 19-20).

The board item contains **EXACTLY THREE** bullets, all DISPLAY-ONLY
(*"skill_view consumption is NOT affected"*). This module pins one node
per bullet and nothing else:

* **E3.1** — ``skill_fix`` echoed a bare 8-hex token (``skill_id[:8]``).
  The 8-hex form is NOT ``skill_view``-consumable, so an agent that
  copied it back into ``skill_view`` failed (the wi-76d89e11
  verbatim-id contract). The echo now carries the **full id**.
* **E3.2** — ``skill_feedback`` echoed the same bare 8-hex token. Same
  treatment.
* **E3.3** — ``_render_pf_view`` printed the internal ``pf-<sha16>``
  pseudo-id on the agent-facing ``- id:`` line. It now prints the
  CONSUMABLE **address** ``pf:<ns>:<name>``, and the pseudo-id stays
  available ADDITIVELY under ``- pseudo_id:`` (the internal key
  continuity the injection path and the embedding rows depend on).

WHAT IS **NOT** PINNED HERE (deliberately): the ``:1213`` refusal-gap
extension, the ``to_summary()`` dead surface and the P7
shared-refusal-helper re-derive. Those three are **leader-judgment
cleanups, NOT-from-board** — out of this lane's scope.

DISPLAY-ONLY means the consumable surfaces must be UNAFFECTED: the
existing ``skill_id_verbatim_contract`` pack (135/135) is the
regression net and must stay green.
"""

from __future__ import annotations

import os
import re

import pytest
from sqlmodel import SQLModel, create_engine
from unittest.mock import AsyncMock, MagicMock

import daemon.repositories.skill.models  # noqa: F401 — legacy models registered
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillFileRepository,
    ProjectSkillUsageRecordRepository,
)
from daemon.services.project_skill_source.service import (
    ProjectSkillSourceService,
)
from daemon.tools.skill_tools import create_skill_tools
from tests.helpers.project_skill_source_fixtures import (
    FakeInstance,
    FakeInstanceRepository,
    FakeProject,
    FakeProjectRepository,
    write_skill,
)

PROJECT_ID = "11111111-2222-3333-4444-555555555555"
INSTANCE_ID = "inst-display-hygiene"

#: A realistic DB skill id — a full uuid4 (the only form ``skill_view``
#: resolves exactly, per the wi-76d89e11 contract).
FULL_SKILL_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"

#: The 8-hex prefix the pre-fix code echoed. NOTE it is a SUBSTRING of
#: the full id, so every assertion below matches on the SURROUNDING
#: shape (a quoted id, or ``skill <id>...``), never on mere presence.
BARE_TOKEN = FULL_SKILL_ID[:8]

#: An 8-hex token that is NOT part of the full id — used as a positive
#: control so the "bare token absent" assertions cannot pass vacuously.
UNRELATED_BARE_TOKEN = "deadbeef"


def _bare_token_shape(token: str) -> str:
    """The exact quoted shape the pre-fix echo produced."""
    return f"'{token}'"


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/display.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def canon(tmp_path):
    """A minimal project-file canon with ONE skill."""
    root = str(tmp_path / "proj-root")
    os.makedirs(root, exist_ok=True)
    write_skill(
        root, "tester", "beta-skill",
        body_text="Beta body.\n",
        manifest={
            "schema_version": 1, "name": "beta-skill",
            "body": "beta-skill.md", "version": "2.0.0",
            "description": "Beta skill.", "category": "workflow",
            "tiers": [1], "loads": [], "assets": [],
        },
    )
    return root


def _make_manager(engine, canon, *, dispatcher_missing: bool):
    """The pf-surface manager shape (real file source, mocked DB services)."""
    file_repo = ProjectSkillFileRepository(engine)
    usage_repo = ProjectSkillUsageRecordRepository(engine)
    fs = ProjectSkillSourceService(
        instance_repository=FakeInstanceRepository(
            {INSTANCE_ID: FakeInstance(INSTANCE_ID, {"project_id": PROJECT_ID}, PROJECT_ID)}
        ),
        project_repository=FakeProjectRepository(
            [FakeProject(PROJECT_ID, main_directory=canon)]
        ),
        file_repository=file_repo,
    )
    manager = MagicMock()
    manager._skill_search_service = MagicMock()
    manager._skill_store_service = MagicMock()
    manager._skill_metrics_service = MagicMock()
    manager._skill_metrics_service.record_feedback = AsyncMock(return_value=True)
    manager._skill_job_dispatcher = None if dispatcher_missing else MagicMock()
    manager._project_skill_source = fs
    manager._project_skill_file_repository = file_repo
    manager._project_skill_usage_repository = usage_repo
    manager._instance_repository = FakeInstanceRepository(
        {INSTANCE_ID: FakeInstance(INSTANCE_ID, {"project_id": PROJECT_ID}, PROJECT_ID)}
    )
    return manager, fs


def _tools(manager):
    return {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}


# =============================================================================
# E3.1 — skill_fix echoes the FULL id
# =============================================================================


class TestE31SkillFixEchoesTheFullId:
    """Board bullet 1 — the fix-request confirmation line."""

    @pytest.mark.asyncio
    async def test_the_echo_carries_the_full_uuid4(self, engine, canon):
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        result = await tools["skill_fix"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "issue_description": "stale regex"}
        )

        assert f"skill '{FULL_SKILL_ID}'" in result, (
            "the fix echo must carry the FULL id (skill_view-consumable "
            f"verbatim): {result!r}"
        )

    @pytest.mark.asyncio
    async def test_the_echo_never_carries_the_bare_8_hex_token(self, engine, canon):
        """The 8-hex shape is NOT ``skill_view``-consumable."""
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        result = await tools["skill_fix"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "issue_description": "stale regex"}
        )

        assert _bare_token_shape(BARE_TOKEN) not in result, (
            "the fix echo must not truncate the id to 8 hex chars: "
            f"{result!r}"
        )

    def test_the_assertion_can_see_a_bare_token(self):
        """Vacuity control for the two pins above.

        The 'bare token absent' assertion matches the QUOTED shape; prove
        that shape is detectable at all, so the pin is not silently
        always-true.
        """
        hypothetical_pre_fix = (
            f"\U0001f4dd Skill fix request recorded for skill '{BARE_TOKEN}'."
        )
        assert _bare_token_shape(BARE_TOKEN) in hypothetical_pre_fix
        assert _bare_token_shape(UNRELATED_BARE_TOKEN) not in hypothetical_pre_fix


# =============================================================================
# E3.2 — skill_feedback echoes the FULL id
# =============================================================================


class TestE32SkillFeedbackEchoesTheFullId:
    """Board bullet 2 — the feedback confirmation lines."""

    @pytest.mark.asyncio
    async def test_the_recorded_line_carries_the_full_uuid4(self, engine, canon):
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        result = await tools["skill_feedback"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "applied": True, "note": "helped"}
        )

        assert f"Feedback recorded for skill {FULL_SKILL_ID}..." in result, (
            f"the feedback echo must carry the FULL id: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_the_recorded_line_never_carries_the_bare_8_hex_token(
        self, engine, canon
    ):
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        result = await tools["skill_feedback"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "applied": True, "note": "helped"}
        )

        assert f"skill {BARE_TOKEN}..." not in result, (
            f"the feedback echo must not truncate the id: {result!r}"
        )


# =============================================================================
# E3.3 — _render_pf_view echoes the ADDRESS, pseudo_id additive
# =============================================================================


class TestE33PfViewEchoesTheAddress:
    """Board bullet 3 — the file-skill view header."""

    @pytest.mark.asyncio
    async def test_the_id_line_carries_the_address(self, engine, canon):
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        rendered = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:beta-skill"}
        )

        assert "- id: pf:tester:beta-skill" in rendered, (
            f"the agent-facing `- id:` line must carry the ADDRESS: {rendered!r}"
        )

    @pytest.mark.asyncio
    async def test_the_pseudo_id_stays_available_additively(self, engine, canon):
        """The internal key stays reachable — deletion is NOT the fix."""
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        rendered = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:beta-skill"}
        )

        assert re.search(r"- pseudo_id: pf-[0-9a-f]{16}\b", rendered), (
            "the internal pseudo-id must stay available under the "
            f"ADDITIVE `- pseudo_id:` line: {rendered!r}"
        )

    @pytest.mark.asyncio
    async def test_the_id_line_never_carries_the_pseudo_id(self, engine, canon):
        """The pseudo-id must not LEAK on the agent-facing `- id:` line."""
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        rendered = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:beta-skill"}
        )

        id_lines = [ln for ln in rendered.splitlines() if ln.startswith("- id: ")]
        assert id_lines, f"the header must carry an `- id:` line: {rendered!r}"
        for line in id_lines:
            assert not re.search(r"pf-[0-9a-f]{16}", line), (
                f"the `- id:` line must not leak the pseudo-id: {line!r}"
            )

    @pytest.mark.asyncio
    async def test_the_emitted_id_is_skill_view_consumable_verbatim(
        self, engine, canon
    ):
        """DISPLAY-ONLY means the emitted id must still ROUND-TRIP.

        The address printed on the ``- id:`` line is fed straight back
        into ``skill_view`` — it must resolve to the same skill (the
        wi-76d89e11 verbatim contract, which is what a truncated or
        internal-only id breaks).
        """
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        first = await tools["skill_view"].ainvoke({"skill_id": "pf:tester:beta-skill"})
        emitted = next(
            ln[len("- id: "):].strip()
            for ln in first.splitlines()
            if ln.startswith("- id: ")
        )
        again = await tools["skill_view"].ainvoke({"skill_id": emitted})
        assert again.splitlines()[0] == "# beta-skill", (
            f"the emitted id {emitted!r} must be verbatim-consumable: {again!r}"
        )
