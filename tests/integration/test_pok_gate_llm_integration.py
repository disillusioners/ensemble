"""POK write-gate — the MARKED live-LLM integration path (Phase 11, T9).

EXCLUDED from the default runs (``pytestmark = pytest.mark.integration``
— the project's default addopts deselect ``-m 'not integration ...'``).
Run it explicitly:

    pytest -m integration tests/integration/test_pok_gate_llm_integration.py

What is REAL here: the judge makes a LIVE LLM call through the house
failover facade, using the purpose-bound ``POK_GATE_MODEL`` alias
(default the literal ``"quick"`` — the proxy resolves it). The verdict
is parsed by the closed-set parser and the outcome is asserted on the
SHAPE, never on a specific verdict (the model's answer is not a
contract).

NO live store: this file NEVER contacts a database. The digest is a
synthetic row list and the exercise asserts the verdict SHAPE only —
nothing is written. The write path is exercised by the unit pack
(tests/unit/tools/test_pok_gate_tool_unit.py).
"""

from __future__ import annotations

import os

import pytest

from daemon.services.pok_gate import (
    GATE_FAMILIES,
    GATE_VERDICTS,
    ActiveRow,
    POKGateUnavailable,
    judge_pok_write,
    resolve_gate_model,
)

pytestmark = pytest.mark.integration


def _rows() -> list[ActiveRow]:
    return [
        ActiveRow(
            slug="deploy-01-smoke-gate",
            body="the deploy smoke gate must pass before the release train moves",
            family="deploy",
        ),
        ActiveRow(
            slug="pok-02-family-cap",
            body="a family holds at most three active entries in the store",
            family="pok",
        ),
    ]


def _llm_config() -> dict:
    """The raw connection config from the ambient environment.

    The integration path needs a REAL proxy: the alias is sent as-is
    (``POK_GATE_MODEL``, default ``"quick"``) and the proxy resolves
    it.
    """
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not base_url or not api_key:
        pytest.skip("no LLM proxy configured (OPENAI_BASE_URL / OPENAI_API_KEY)")
    return {"base_url": base_url, "api_key": api_key}


class TestLiveJudge:
    def test_the_live_judge_returns_a_closed_set_verdict(self) -> None:
        """The live call parses into the CLOSED verdict + family sets."""
        verdict = judge_pok_write(
            project_id="integration",
            project_name="the integration project",
            slug="deploy-02-rollback-rule",
            body="when a rollback is needed the release owner re-runs the smoke gate",
            reason="the rollback path lost a step and the team had to rediscover it",
            rows=_rows(),
            llm_config=_llm_config(),
            mode="llm",
            is_production=False,
        )
        assert verdict.verdict in GATE_VERDICTS
        assert verdict.family in GATE_FAMILIES
        assert verdict.model_alias == resolve_gate_model()
        assert verdict.latency_ms >= 0

    def test_the_live_judge_answers_the_gate_question(self) -> None:
        """A second live call — the shape is stable; the verdict is not
        a contract."""
        verdict = judge_pok_write(
            project_id="integration",
            project_name="the integration project",
            slug="deploy-03-freeze-window",
            body="the release freeze window is owned by the release captain",
            reason="two freezes overlapped and nobody knew who owned the window",
            rows=_rows(),
            llm_config=_llm_config(),
            mode="llm",
            is_production=False,
        )
        assert verdict.verdict in GATE_VERDICTS
        assert verdict.family in GATE_FAMILIES
        if verdict.verdict == "REJECT":
            # R5 — a rejection names an alternative home.
            assert verdict.alternative

    def test_the_live_judge_never_answers_unavailable_for_a_shape_failure(
        self,
    ) -> None:
        """A D2 refusal is TYPED — the caller can tell it apart from a
        REJECT."""
        from daemon.services.pok_gate import parse_verdict_payload

        with pytest.raises(POKGateUnavailable):
            parse_verdict_payload("definitely not a verdict")
