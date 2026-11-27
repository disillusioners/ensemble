"""F10 pins — change_summary HYGIENE (skill-evolution R1).

THE GAP THIS CLOSES

``SkillLineage.change_summary`` carried the ENTIRE evolution direction
(``f"FIX: {direction}"`` / ``f"CORRECTIVE FIX: ..."`` / ``f"DERIVED:
..."``). The FIX dispatcher composes the direction as the user's
``issue_description`` PLUS an appended ``Suggested fix:`` block —
arbitrarily long, multi-paragraph input — and the column is an
UNBOUNDED ``TEXT NOT NULL DEFAULT ''`` (models.py:289 — an AutoString
with NO ``max_length``; migration 20260710_000001, line 38). F10 stores
the FIRST LINE of the direction, hard-capped IN CODE (the column cannot
cap it), with the full direction still living in job metadata.

CHOSEN CAP: 200 chars (``_MAX_CHANGE_SUMMARY_CHARS``). Justification
is carried in the constant's docstring: the summary is a one-line
human-facing label rendered beside lineage edges, and it is
deliberately narrower than ``_MAX_NOTE_CHARS`` (300) and
``_MAX_PAYLOAD_DESCRIPTION_CHARS`` (512).

``_evolve_captured`` writes NO change_summary (only the three
``_lineage_repo.create`` sites in FIX / CORRECTIVE FIX / DERIVED) — the
spec leaves that AS IS and so does this lane.

NAMED MUTATION LEGS (each turns this file RED):

* **M-F10a** — reinstate the full ``direction`` in the summary → the
  first-line / cap tests RED.
* **M-F10b** — remove the cap (no truncation) → the length test REDs.
* **M-F10c** — revert one of the three sites to the raw f-string → the
  all-three-sites test REDs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from daemon.repositories.skill.repository import (
    SkillABTestRepository,
    SkillLineageRepository,
    SkillRepository,
    SkillUsageRepository,
)
from daemon.services.skill_evolution_service import (
    SkillEvolutionService,
    _MAX_CHANGE_SUMMARY_CHARS,
    _change_summary,
)

PROJECT = "p-f10-summary"


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'f10.db'}",
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
def lineage_repo(engine):
    return SkillLineageRepository(engine)


@pytest.fixture
def svc(engine, skill_repo, lineage_repo):
    embedding = MagicMock()
    embedding.update_skill_embeddings = AsyncMock(return_value=None)
    return SkillEvolutionService(
        skill_repo=skill_repo,
        lineage_repo=lineage_repo,
        usage_repo=SkillUsageRepository(engine),
        embedding_service=embedding,
        metrics_service=MagicMock(),
        ab_test_repo=SkillABTestRepository(engine),
        config=SimpleNamespace(
            analysis_model="m", evolution_model="m",
            ab_sample_size=10, ab_min_difference=0.15, max_extensions=3,
            capture_min_iterations=5, capture_min_duration_seconds=60,
            ab_stall_timeout_days=7.0,
        ),
        llm_config={"base_url": "http://t", "api_key": "k", "model": "m"},
    )


def _make_skill(skill_repo, name="f10-skill"):
    return skill_repo.create(
        name=name, description="d", content="alpha\nbeta\n",
        project_id=PROJECT,
    )


# ── The helper contract ───────────────────────────────────────────────────


class TestChangeSummaryHelper:
    def test_keeps_only_the_first_line(self):
        out = _change_summary(
            "FIX", "tighten error handling\n\nSuggested fix: add a retry loop\nand more"
        )
        assert out == "FIX: tighten error handling"
        assert "\n" not in out

    def test_caps_at_the_declared_limit(self):
        direction = "x" * (3 * _MAX_CHANGE_SUMMARY_CHARS)
        out = _change_summary("DERIVED", direction)
        assert len(out) <= _MAX_CHANGE_SUMMARY_CHARS
        assert out.endswith("\u2026"), "a truncated summary is MARKED as truncated"
        assert out.startswith("DERIVED: x")

    def test_empty_direction_yields_the_prefix_alone(self):
        assert _change_summary("FIX", "") == "FIX"
        assert _change_summary("FIX", "   \n  ") == "FIX"

    def test_the_cap_is_200_and_documented(self):
        assert _MAX_CHANGE_SUMMARY_CHARS == 200


# ── The three production sites use it ────────────────────────────────────


class TestAllThreeSites:
    @pytest.mark.asyncio
    async def test_fix_lineage_summary_is_first_line_only(
        self, svc, skill_repo, lineage_repo
    ):
        parent = _make_skill(skill_repo)
        script = json.dumps(
            {"edits": [{"op": "replace", "anchor": "beta", "replacement": "BETA"}]}
        )
        direction = (
            "tighten the error section\n\nSuggested fix: add retries\n"
            + "extra prose\n" * 50
        )
        with patch.object(svc, "_call_llm", AsyncMock(return_value=script)):
            result = await svc.evolve_skill(parent.id, "FIX", direction)

        edge = lineage_repo.get_parents(result["new_skill_id"])[0]
        summary = edge.change_summary
        assert summary.startswith("FIX: tighten the error section")
        assert "\n" not in summary, (
            f"the summary must be ONE line, got {summary!r}"
        )
        assert "Suggested fix" not in summary
        assert len(summary) <= _MAX_CHANGE_SUMMARY_CHARS

    @pytest.mark.asyncio
    async def test_derived_lineage_summary_is_first_line_only(
        self, svc, skill_repo, lineage_repo
    ):
        parent = _make_skill(skill_repo, name="f10-derived")
        script = json.dumps(
            {"edits": [{"op": "replace", "anchor": "alpha", "replacement": "ALPHA"}]}
        )
        with patch.object(svc, "_call_llm", AsyncMock(return_value=script)):
            result = await svc.evolve_skill(
                parent.id, "DERIVED", "specialize for CI\nsecond line"
            )
        edge = lineage_repo.get_parents(result["new_skill_id"])[0]
        assert edge.change_summary == "DERIVED: specialize for CI"

    def test_corrective_site_uses_the_helper(self):
        """Source pin: ALL THREE lineage-create sites route through
        ``_change_summary`` — no f-string site survives."""
        import inspect

        src = inspect.getsource(SkillEvolutionService)
        assert 'change_summary=_change_summary("FIX", direction)' in src
        assert 'change_summary=_change_summary("CORRECTIVE FIX", direction)' in src
        assert 'change_summary=_change_summary("DERIVED", direction)' in src
        assert 'change_summary=f"FIX: {direction}"' not in src
        assert 'change_summary=f"CORRECTIVE FIX: {direction}"' not in src
        assert 'change_summary=f"DERIVED: {direction}"' not in src
