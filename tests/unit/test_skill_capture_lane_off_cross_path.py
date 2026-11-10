"""The CROSS-PATH pin — EVERY production capture-lane-OFF refusal site counts
(round 8; the tester-vs-lane contradiction, resolved against the CODE).

THE QUESTION THIS MODULE ANSWERS.  Does the ``capture_lane_off`` ADMISSION
counter move on EVERY production path where a capture-lane-OFF refusal can
occur — or is it PARTIAL (as the tester gate's §7 claimed)?

THE ENUMERATION (driven from the CODE, not from prose).  The capture
kill-switch is READ in exactly four places in ``daemon/``; three of them are
REFUSAL sites and one is a READ:

1. ``daemon/services/skill_metrics_service.py:659`` — the METRICS seam.
   ``_check_capture_eligibility`` Gate 0 → the recorder fires BEFORE the
   ``return None`` (:671).  COUNTED.
2. ``daemon/tools/skill_evolution_tools.py:522`` — the TOOLS seam.
   ``skill_execute_capture`` → the recorder fires BEFORE the ``skipped``
   envelope.  COUNTED.
3. ``daemon/services/skill_job_dispatcher.py:1126`` — the DISPATCHER gate.
   ``enqueue_capture`` → ``record_admission_refusal("capture_lane_off")`` +
   the sink record, BEFORE the no-op return.  COUNTED (the SAME counter key).
4. ``daemon/services/skill_roster.py:382`` — a READ (the roster reports
   ``skill_capture_enabled``); it refuses nothing ⇒ not a refusal site.

**THE TWIN SEAM IS NOT A FOURTH SITE.**  ``SkillEvolutionService.
check_and_capture`` has NO capture-enable check at all (its gates are
task-success, non-blank message, non-blank agent, the trivial filter and
the A3 evolved-predicate) ⇒ it never refuses *because the capture lane is
off* ⇒ there is no uncounted refusal there.  This module PINS that fact
(``test_the_twin_seam_has_no_capture_off_refusal``) so the claim cannot
drift back.

WHY A CROSS-PATH PIN AND NOT A SINGLE-PATH ONE.  A pin that exercises only
the tools path would stay GREEN while the metrics path went dark — the
exact failure class this module exists to close.  Every refusal path is
driven here, and each is asserted SEPARATELY, so a defect in one path
cannot be masked by the other two.

WHY THE VACUITY CONTROL.  The two seams that belong to other lanes are
proven WITHOUT planting in their files (the house rule: a transient plant
in another lane's file can revert their commit on restore).  Instead the
counter is neutralised in-test: with the counting removed, the path's
counter does NOT move — so the assertion above it provably depends on the
counting.

Mutation legs (pack ``metrics_capture_lane_off_seam_unit_test``):

* ``M-C2e`` — remove the metrics-seam recorder call ⇒ ONLY the metrics
  path's assertion REDs; the tools and dispatcher paths stay GREEN (the
  path-sensitivity proof);
* the leak leg ``M-C2d`` (the observability sink) lives in the same pack.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from daemon.services import skill_capture_observability as observability
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SkillMetricsService,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The counter key every capture-lane-OFF refusal moves.
COUNTER_KEY = "capture_lane_off"

#: The production guard SITES this module drives — the ENUMERATION.
#: (file, the line the guard sits on) — kept in sync with the code by
#: ``test_every_production_capture_off_guard_is_pinned`` (an AST walk).
EXPECTED_GUARDS = {
    ("daemon/services/skill_metrics_service.py", 659),
    ("daemon/tools/skill_evolution_tools.py", 522),
    ("daemon/services/skill_job_dispatcher.py", 1126),
}


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path):
    """The dispatcher gate writes a SINK record — redirect it (L7)."""
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    observability.set_anomaly_reporter(None)
    try:
        yield tmp_path
    finally:
        observability.configure_sink(None)
        observability.reset_counters()
        observability.set_anomaly_reporter(None)


@pytest.fixture(autouse=True)
def _capture_lane_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The capture lane is OFF — the state under test (default OFF)."""
    monkeypatch.delenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", raising=False)


def _admission_refusals() -> dict[str, int]:
    return dict(observability.counters().admission_refusals)


class _Config:
    capture_min_iterations = 5
    capture_min_duration_seconds = 60


def _metrics_service():
    return SkillMetricsService(
        usage_repo=SimpleNamespace(
            has_genuinely_evolved_for_instance=lambda instance_id: False
        ),
        skill_repo=SimpleNamespace(),
        trigger_repo=SimpleNamespace(),
        ab_test_repo=SimpleNamespace(),
        config=_Config(),
        instance_repo=SimpleNamespace(
            get=lambda instance_id: SimpleNamespace(
                instance_id=instance_id,
                instance_metadata={INJECTED_SKILLS_METADATA_KEY: ["skill-1"]},
            )
        ),
        evolution_service=SimpleNamespace(),
        agent_id_resolver=lambda agent_id, instance_id=None: SimpleNamespace(
            skill_injection=True
        ),
    )


async def _drive_metrics_seam() -> None:
    """Path 1 — the METRICS seam (Gate 0 of the eligibility check)."""
    service = _metrics_service()
    assert (
        await service._check_capture_eligibility(
            instance_id="inst-cross-path",
            agent_id="agent-cross-path",
            project_id="p-cross-path",
            task_message="a cross-path canary",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
        )
        is None
    ), "the gate must refuse while the capture lane is off"


async def _drive_tools_seam() -> None:
    """Path 2 — the TOOLS seam (the ``skill_execute_capture`` tool)."""
    from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

    manager = MagicMock()
    manager._skill_evolution_service = None
    manager._instance_repository = None
    tools = {
        getattr(tool, "name", None): tool
        for tool in create_skill_evolution_tools(manager, "test-instance")
    }
    result = await tools["skill_execute_capture"].ainvoke(
        {
            "instance_id": "inst-cross-path",
            "task_message": "a cross-path canary",
            "iterations": 10,
            "duration_seconds": 120,
        }
    )
    assert "skipped" in str(result), (
        "the tool must refuse while the capture lane is off"
    )


async def _drive_dispatcher_gate() -> None:
    """Path 3 — the DISPATCHER gate (``enqueue_capture``)."""
    from daemon.services.skill_job_dispatcher import SkillJobDispatcher

    dispatcher = SkillJobDispatcher(
        job_service=MagicMock(),
        queue_repo=MagicMock(),
    )
    job_id = await dispatcher.enqueue_capture(
        project_id="p-cross-path",
        task_details={
            "instance_id": "inst-cross-path",
            "agent_id": "agent-cross-path",
            "project_id": "p-cross-path",
            "task_message": "a cross-path canary",
            "iterations": 10,
            "duration_seconds": 120,
        },
    )
    assert job_id is None, (
        "the dispatcher must refuse (no job) while the capture lane is off"
    )


#: The three paths, by name — the pin drives each one SEPARATELY.
PATHS = {
    "metrics_seam": _drive_metrics_seam,
    "tools_seam": _drive_tools_seam,
    "dispatcher_gate": _drive_dispatcher_gate,
}


class TestEveryCaptureOffPathIsCounted:
    """One assertion per refusal path — a defect in one cannot hide."""

    @pytest.mark.asyncio
    async def test_the_metrics_seam_counts_the_refusal(self) -> None:
        observability.reset_counters()
        await _drive_metrics_seam()
        assert _admission_refusals().get(COUNTER_KEY) == 1, (
            "the METRICS seam must count the capture-lane-off refusal — "
            "Gate 0 refuses BEFORE the dispatcher's gate is reached, so "
            "this path would go uncounted if the recorder were removed"
        )

    @pytest.mark.asyncio
    async def test_the_tools_seam_counts_the_refusal(self) -> None:
        observability.reset_counters()
        await _drive_tools_seam()
        assert _admission_refusals().get(COUNTER_KEY) == 1, (
            "the TOOLS seam must count the capture-lane-off refusal"
        )

    @pytest.mark.asyncio
    async def test_the_dispatcher_gate_counts_the_refusal(self) -> None:
        observability.reset_counters()
        await _drive_dispatcher_gate()
        assert _admission_refusals().get(COUNTER_KEY) == 1, (
            "the DISPATCHER gate must count the capture-lane-off refusal "
            "(it counts through record_admission_refusal with the SAME "
            "counter key)"
        )


class TestTheEnumerationIsPinned:
    """The census: a NEW capture-off site without a pin must RED this."""

    def test_every_production_capture_off_guard_is_pinned(self) -> None:
        """Every capture-off GUARD is pinned — found by an AST walk.

        The census is AST-based (not a text grep): a docstring that
        QUOTES the guard (``daemon/services/skill_capture_observability.py``
        quotes it in prose) must not be counted as a refusal site.
        """
        found: set[tuple[str, int]] = set()
        for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                test = node.test
                if not isinstance(test, ast.UnaryOp) or not isinstance(
                    test.op, ast.Not
                ):
                    continue
                operand = test.operand
                if not isinstance(operand, ast.Call):
                    continue
                func = operand.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else getattr(func, "attr", "")
                )
                if name == "_resolve_capture_enabled":
                    found.add(
                        (
                            str(path.relative_to(REPO_ROOT)),
                            node.lineno,
                        )
                    )
        assert {path for path, _ in found} == {
            path for path, _ in EXPECTED_GUARDS
        }, (
            "the capture-off refusal SITES changed — a NEW guard (or a "
            "moved one) must be driven by a pin in this module, and the "
            f"expected set must be updated with it. found={sorted(found)}"
        )
        assert len(found) == len(EXPECTED_GUARDS), (
            f"one of the pinned sites grew a SECOND capture-off guard: "
            f"found={sorted(found)}"
        )

    def test_the_twin_seam_has_no_capture_off_refusal(self) -> None:
        """The twin seam refuses for OTHER reasons — never for capture-off."""
        source = (
            REPO_ROOT
            / "daemon"
            / "services"
            / "skill_evolution_service.py"
        ).read_text(encoding="utf-8")
        assert "_resolve_capture_enabled" not in source, (
            "the twin seam gained a capture-off refusal — if it refuses "
            "without counting, the counter IS partial and this module "
            "must drive it"
        )


class TestThePinIsNotVacuous:
    """With the counting removed, the counter does NOT move."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path_name", sorted(PATHS))
    async def test_neutralising_the_counting_stops_the_counter(
        self, path_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observability.reset_counters()
        monkeypatch.setattr(
            observability,
            "record_capture_lane_disabled_refusal",
            lambda **kwargs: None,
            raising=True,
        )
        monkeypatch.setattr(
            observability,
            "record_admission_refusal",
            lambda *args, **kwargs: None,
            raising=True,
        )
        await PATHS[path_name]()
        assert _admission_refusals().get(COUNTER_KEY) is None, (
            f"the {path_name} path moved the counter even with the counting "
            f"neutralised — the assertion above it proves nothing"
        )
