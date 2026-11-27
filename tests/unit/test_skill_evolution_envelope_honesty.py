"""F6 pins — skill_evolve envelope HONESTY (skill-evolution R1).

THE GAPS THIS CLOSES (F6b — the ``meta`` key shape at the two
``_produce_evolved_body`` sites — is HELD OUT OF SCOPE; nothing here
pins it)

* ``_evolve_captured`` never runs the surgical pipeline, and its
  returned dicts carried NONE of the edit-contract keys — while the
  documented shape promised nothing more either. F6 makes CAPTURED
  return the SAME edit-contract keys, explicitly neutral.
* The full-regen branch's meta carried ``{"mode": "full_regen"}`` ONLY —
  no edit counts, no rejections.
* A surgical evolution whose every op was rejected raised a bare
  ``ValueError`` whose rejection reasons reached the agent ONLY as
  prose inside ``_invoke_service``'s ``ERROR: ...`` string. F6 raises
  the structured ``EvolutionEditRejected`` and the tool lane
  serialises its JSON envelope.
* ``partial_apply`` did not exist: a caller could not tell a COMPLETE
  evolution from a PARTIAL one from the returned shape.
* The documented shapes (``evolve_skill`` docstring + the tool's
  ``_full_doc_``) omitted ``mode`` / ``edits_*`` and the real
  ``corrective`` parameter.

ACCEPTANCE: a caller must be able to tell a COMPLETE evolution from a
PARTIAL one (and from a REFUSED one) by reading the returned shape
ALONE.

NAMED MUTATION LEGS (each turns this file RED):

* **M-F6a** — drop ``partial_apply`` from the surgical meta.
* **M-F6b** — drop the edit-contract keys from ``_evolve_captured``.
* **M-F6c** — revert the zero-applied raise to a bare ``ValueError``
  without ``.reasons`` / ``.to_payload``.
* **M-F6d** — restore the prose-only ``ERROR:`` rendering in
  ``_invoke_service`` (remove the structured ``except``).
* **M-F6e** — remove ``_full_doc_``'s edit-contract / ``corrective``
  documentation.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

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
from daemon.services import skill_capture_observability as observability
from daemon.services.skill_evolution_service import (
    EvolutionEditRejected,
    SkillEvolutionService,
)
from tests.helpers.skill_sink_redirect import (
    lane_test_end,
    lane_test_start,
    restore_safe_sink_redirect,
    snapshot_real_sink_dir,
)

PROJECT = "p-f6-envelope"
INSTANCE_ID = "inst-f6-1"

EDIT_CONTRACT_KEYS = {
    "mode",
    "edits_applied",
    "edits_rejected",
    "edit_rejections",
    "partial_apply",
}


# ── Harness ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path, request):
    lane_test_start(request.node.nodeid)
    before = snapshot_real_sink_dir()
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    observability.set_anomaly_reporter(None)
    try:
        yield tmp_path
    finally:
        restore_safe_sink_redirect()
        observability.reset_counters()
        observability.set_anomaly_reporter(None)
        lane_test_end(request.node.nodeid, before)


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'f6-envelope.db'}",
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
def svc(engine, skill_repo):
    embedding = MagicMock()
    embedding.update_skill_embeddings = AsyncMock(return_value=None)
    return SkillEvolutionService(
        skill_repo=skill_repo,
        lineage_repo=SkillLineageRepository(engine),
        usage_repo=SkillUsageRepository(engine),
        embedding_service=embedding,
        metrics_service=MagicMock(),
        ab_test_repo=SkillABTestRepository(engine),
        config=SimpleNamespace(
            analysis_model="m",
            evolution_model="m",
            ab_sample_size=10,
            ab_min_difference=0.15,
            max_extensions=3,
            capture_min_iterations=5,
            capture_min_duration_seconds=60,
            ab_stall_timeout_days=7.0,
        ),
        llm_config={"base_url": "http://test", "api_key": "k", "model": "m"},
    )


def _make_skill(skill_repo, name="f6-skill", content=None):
    return skill_repo.create(
        name=name,
        description="d",
        content=content or "alpha\nbeta\ngamma\n",
        project_id=PROJECT,
    )


def _sink_records(tmp_path):
    sink_dir = tmp_path / observability.SINK_DIR_NAME
    records = []
    for path in sorted(sink_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


# ── The success shape — COMPLETE vs PARTIAL ──────────────────────────────


class TestSurgicalSuccessShape:
    @pytest.mark.asyncio
    async def test_complete_apply_carries_the_full_edit_contract(
        self, svc, skill_repo
    ):
        parent = _make_skill(skill_repo)
        script = json.dumps(
            {"edits": [{"op": "replace", "anchor": "beta", "replacement": "BETA"}]}
        )
        with patch.object(svc, "_call_llm", AsyncMock(return_value=script)):
            result = await svc.evolve_skill(parent.id, "FIX", "swap beta")

        assert EDIT_CONTRACT_KEYS <= set(result)
        assert result["mode"] == "surgical"
        assert result["edits_applied"] == 1
        assert result["edits_rejected"] == 0
        assert result["edit_rejections"] == []
        assert result["partial_apply"] is False  # COMPLETE

    @pytest.mark.asyncio
    async def test_partial_apply_is_visible_in_the_shape(
        self, svc, skill_repo
    ):
        parent = _make_skill(skill_repo)
        script = json.dumps(
            {
                "edits": [
                    {"op": "replace", "anchor": "beta", "replacement": "BETA"},
                    {"op": "replace", "anchor": "NOPE", "replacement": "x"},
                ]
            }
        )
        with patch.object(svc, "_call_llm", AsyncMock(return_value=script)):
            result = await svc.evolve_skill(parent.id, "FIX", "swap beta")

        assert result["edits_applied"] == 1
        assert result["edits_rejected"] == 1
        assert result["edit_rejections"], "the rejected op must be NAMED"
        assert result["partial_apply"] is True  # PARTIAL

    @pytest.mark.asyncio
    async def test_full_regen_carries_the_contract_keys(self, svc, skill_repo):
        parent = _make_skill(skill_repo)
        with patch.object(
            svc, "_call_llm", AsyncMock(return_value="rewritten body\nlines")
        ):
            result = await svc.evolve_skill(
                parent.id, "FIX", "rewrite the whole body from scratch"
            )
        assert result["mode"] == "full_regen"
        assert EDIT_CONTRACT_KEYS <= set(result)
        assert result["edits_applied"] == 0
        assert result["edits_rejected"] == 0
        assert result["edit_rejections"] == []
        assert result["partial_apply"] is False


# ── CAPTURED joins the contract ──────────────────────────────────────────


class TestCapturedEnvelope:
    def _details(self, **over):
        base = {
            "task_message": "do the thing",
            "iterations": 3,
            "duration_seconds": 90,
            "agent_id": "worker",
            "project_id": PROJECT,
        }
        base.update(over)
        return base

    @pytest.mark.asyncio
    async def test_capture_success_carries_neutral_edit_keys(
        self, svc, skill_repo
    ):
        payload = json.dumps(
            {
                "name": "captured-skill",
                "description": "captured desc",
                "content": (
                    "## Procedure\n"
                    "1. Read the task message and identify the goal.\n"
                    "2. Apply the documented checklist step by step.\n"
                    "3. Verify the outcome and report the result.\n"
                ),
            }
        )
        with patch.object(svc, "_call_llm", AsyncMock(return_value=payload)):
            result = await svc._evolve_captured(self._details())

        assert result["skipped"] is False
        assert EDIT_CONTRACT_KEYS <= set(result)
        assert result["mode"] == "captured"
        assert result["edits_applied"] == 0
        assert result["edits_rejected"] == 0
        assert result["edit_rejections"] == []
        assert result["partial_apply"] is False

    @pytest.mark.asyncio
    async def test_capture_skip_carries_the_same_keys(
        self, svc, skill_repo
    ):
        result = await svc._evolve_captured(self._details(task_message="  "))
        assert result["skipped"] is True
        assert result["skip_reason"] == "blank_task_message"
        assert EDIT_CONTRACT_KEYS <= set(result)
        assert result["mode"] == "captured"


# ── The refused shape — STRUCTURED, not prose ────────────────────────────


class TestRefusedShapeIsStructured:
    @pytest.mark.asyncio
    async def test_zero_applied_raise_carries_structured_reasons(
        self, svc, skill_repo, tmp_path
    ):
        parent = _make_skill(skill_repo)
        script = json.dumps(
            {"edits": [{"op": "replace", "anchor": "NOPE", "replacement": "x"}]}
        )
        with patch.object(svc, "_call_llm", AsyncMock(return_value=script)):
            with pytest.raises(EvolutionEditRejected) as excinfo:
                await svc.evolve_skill(parent.id, "FIX", "fix something")

        exc = excinfo.value
        # It IS a ValueError (existing catches keep working)…
        assert isinstance(exc, ValueError)
        # …and carries the reasons as DATA.
        assert exc.reasons and "anchor occurs 0x" in exc.reasons[0]
        assert exc.edits_rejected == 1
        payload = exc.to_payload()
        assert payload["ok"] is False
        assert payload["reason"] == "no_applicable_edits"
        assert payload["edit_rejections"] == exc.reasons
        assert payload["partial_apply"] is False
        assert EDIT_CONTRACT_KEYS <= set(payload)

        # The refused execution is LOGGED + PERSISTED (house mechanism).
        assert (
            observability.counters().execution_refusals.get(
                "no_applicable_edits"
            )
            == 1
        )
        records = _sink_records(tmp_path)
        assert any(
            r.get("stage") == "execution"
            and r.get("refusal_reason") == "no_applicable_edits"
            and r.get("skill_id") == parent.id
            for r in records
        ), f"the refusal must leave a sink record — got {records!r}"

    @pytest.mark.asyncio
    async def test_tool_lane_renders_the_structured_json_not_prose(self):
        """``_invoke_service`` serialises the refusal payload — an agent
        reads JSON keys, not ``ERROR: ...`` prose."""
        from daemon.tools.skill_evolution_tools import (
            create_skill_evolution_tools,
        )

        manager = MagicMock()
        service = MagicMock()

        async def _raise(*args, **kwargs):
            raise EvolutionEditRejected(
                ["edits[0]: anchor 'NOPE' not found"], edits_rejected=1
            )

        service.evolve_skill = _raise
        manager._skill_evolution_service = service
        tools = {
            t.name: t
            for t in create_skill_evolution_tools(manager, INSTANCE_ID)
        }
        # The lane gate must be ON for the tool to reach the service.
        manager.is_skill_evolution_lane_enabled = MagicMock(return_value=True)

        out = await tools["skill_evolve"].ainvoke(
            {
                "skill_id": "sk-1",
                "evolution_type": "FIX",
                "direction": "fix it",
            }
        )

        assert not out.startswith("ERROR:"), (
            f"the refusal must not be flattened into prose — got {out!r}"
        )
        payload = json.loads(out)
        assert payload["ok"] is False
        assert payload["reason"] == "no_applicable_edits"
        assert payload["edit_rejections"] == [
            "edits[0]: anchor 'NOPE' not found"
        ]


# ── The documented shape matches reality ─────────────────────────────────


class TestDocumentedShape:
    def test_full_doc_documents_the_edit_contract_and_corrective(self):
        from daemon.tools.skill_evolution_tools import (
            create_skill_evolution_tools,
        )

        manager = MagicMock()
        tools = {
            t.name: t
            for t in create_skill_evolution_tools(manager, INSTANCE_ID)
        }
        doc = tools["skill_evolve"]._full_doc_

        for key in ("mode", "edits_applied", "edits_rejected",
                    "edit_rejections", "partial_apply"):
            assert key in doc, f"_full_doc_ must document {key!r}"
        assert "corrective" in doc, (
            "_full_doc_ must document the `corrective` parameter "
            "(a real signature arg that was missing from the doc)"
        )

    def test_service_docstring_documents_the_edit_contract(self):
        doc = SkillEvolutionService.evolve_skill.__doc__ or ""
        for key in ("mode", "edits_applied", "edits_rejected",
                    "edit_rejections", "partial_apply"):
            assert key in doc, f"evolve_skill docstring must name {key!r}"
        assert "EvolutionEditRejected" in doc, (
            "the docstring must name the structured refusal exception"
        )

    def test_every_tier3_result_shape_carries_the_contract(self):
        """SOURCE-LEVEL guard: every return site in the three Tier-3
        paths goes through a shape that supplies the edit contract —
        the documented keys can never again be promised by a path that
        does not return them."""
        import inspect

        src = inspect.getsource(SkillEvolutionService)
        # The CAPTURED path funnels every return through the one
        # envelope builder; the FIX/DERIVED paths update their result
        # with `production_meta`, whose two producers ALWAYS carry the
        # contract keys.
        assert "_captured_envelope(" in src
        assert '"partial_apply": rejected > 0' in src
        assert '"partial_apply": False' in src


# ── DERIVED behavioural shape pin (F6 follow-up, m-7) ─────────────────────


class TestDerivedEnvelopeShape:
    @pytest.mark.asyncio
    async def test_derived_surgical_carries_the_full_edit_contract(
        self, svc, skill_repo
    ):
        parent = _make_skill(skill_repo)
        script = json.dumps(
            {"edits": [{"op": "replace", "anchor": "beta", "replacement": "BETA"}]}
        )
        with patch.object(svc, "_call_llm", AsyncMock(return_value=script)):
            result = await svc.evolve_skill(parent.id, "DERIVED", "derive")

        assert EDIT_CONTRACT_KEYS <= set(result)
        assert result["mode"] in ("surgical", "full_regen")
        assert isinstance(result["edits_applied"], int)
        assert result["edits_applied"] >= 0
        assert isinstance(result["edits_rejected"], int)
        assert result["edits_rejected"] >= 0
        assert isinstance(result["edit_rejections"], list)
        if result["edits_rejected"] == 0:
            assert result["partial_apply"] is False
        else:
            assert result["partial_apply"] is True
        assert "new_skill_id" in result
        assert "parent_ids" in result
        assert isinstance(result["parent_ids"], list)
        assert len(result["parent_ids"]) == 1

    @pytest.mark.asyncio
    async def test_derived_surgical_exact_counts(self, svc, skill_repo):
        parent = _make_skill(skill_repo)
        script = json.dumps(
            {"edits": [{"op": "replace", "anchor": "beta", "replacement": "BETA"}]}
        )
        with patch.object(svc, "_call_llm", AsyncMock(return_value=script)):
            result = await svc.evolve_skill(parent.id, "DERIVED", "derive")

        assert result["mode"] == "surgical"
        assert result["edits_applied"] == 1
        assert result["edits_rejected"] == 0
        assert result["edit_rejections"] == []
        assert result["partial_apply"] is False
