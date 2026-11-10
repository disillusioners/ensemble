"""I2 — the C2 METRICS-LANE refusal seam (``capture_lane_off`` completion).

THE GAP THIS PINS.  ``SkillMetricsService._check_capture_eligibility`` Gate 0
returns ``None`` when ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` is off — BEFORE the
dispatcher's own gate is ever reached.  The dispatcher counts its own
refusals, and the tools lane calls the observability helper at its seam, so
``admission_refusals['capture_lane_off']`` was **PARTIAL**: it moved for the
tools lane only and NEVER for the production metrics-lane path.  The fix is
to call ``observability.record_capture_lane_disabled_refusal(...)`` at Gate 0.

WHY THE PIN IS BEHAVIOURAL AND NOT A GREP.  "The call exists" is not the
property. The property is **the refusal is COUNTED, at the RIGHT MOMENT** —
i.e. the recorder runs BEFORE the early ``return``. A refactor that keeps the
call but moves the ``return`` above it restores exactly the original defect
while every textual assertion still passes.

Mutation legs (pack ``metrics_capture_lane_off_seam_unit_test``):

* ``M-C2a`` — delete the recorder call ⇒ the "counts the refusal" pin REDs;
* ``M-C2b`` — move the ``return None`` ABOVE the recorder call (the pre-fix
  order) ⇒ the ordering pin REDs while the call still exists;
* ``M-C2c`` — drop the counters increment inside the observability helper
  (a plant in ANOTHER LANE'S file — proven here read-only by monkeypatch,
  never by editing the file) ⇒ the real-counter pin REDs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from daemon.services import skill_capture_observability as observability
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SkillMetricsService,
)


class _Config:
    capture_min_iterations = 5
    capture_min_duration_seconds = 60


def _service():
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


@pytest.fixture(autouse=True)
def _lane_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate 0 OFF — simulated by env, never by editing a switch file."""
    monkeypatch.delenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", raising=False)


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path):
    """Point the sink at a scratch dir + reset the counters per test.

    THE LEAK THIS CLOSES (E1/D lane's L7 finding, 2026-11-10).  Without a
    sink destination, EVERY test in this file that drives the production
    refusal seam wrote its ``skill-capture-*.jsonl`` record into the
    CHECKOUT's ``data/skill-capture-observability/`` tree — ~1,260 B per
    run, invisible to ``git status`` (the tree is gitignored), the residue
    gate and the staging gate.  The fix is the sanctioned API
    (``observability.configure_sink`` — the E1/D lane's L7 pattern); the
    data-dir precedence chain is NOT re-implemented here.

    The sink override wins over the environment
    (``resolve_sink_dir`` consults ``_SINK_DIR_OVERRIDE`` first), so the
    write lands under ``tmp_path`` even in the test that sets
    ``ENSEMBLE_DATA_DIR`` itself.
    """
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    observability.set_anomaly_reporter(None)
    try:
        yield tmp_path
    finally:
        observability.configure_sink(None)
        observability.reset_counters()
        observability.set_anomaly_reporter(None)


async def _drive(service, *, task_message: str = "a prompt"):
    return await service._check_capture_eligibility(
        instance_id="inst-c2",
        agent_id="agent-c2",
        project_id="p-c2",
        task_message=task_message,
        task_succeeded=True,
        iterations=10,
        duration_seconds=120,
    )


class TestTheRefusalIsCounted:
    """The headline: the production metrics-lane path moves the counter."""

    @pytest.mark.asyncio
    async def test_the_metrics_lane_refusal_moves_the_admission_counter(
        self,
    ) -> None:
        service = _service()
        assert await _drive(service) is None
        assert (
            observability.counters().admission_refusals.get("capture_lane_off")
            == 1
        ), (
            "the production capture refusal (metrics lane, Gate 0) must "
            "increment the capture_lane_off ADMISSION counter — otherwise "
            "the counter is PARTIAL and only the tools lane moves it"
        )

    @pytest.mark.asyncio
    async def test_each_refusal_counts_once(self) -> None:
        service = _service()
        await _drive(service)
        await _drive(service)
        await _drive(service)
        assert (
            observability.counters().admission_refusals.get("capture_lane_off")
            == 3
        )

    @pytest.mark.asyncio
    async def test_an_admission_does_not_touch_the_refusal_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The negative direction: lane ON ⇒ no refusal counted."""
        monkeypatch.setenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", "1")
        service = _service()
        service.evolution_service = SimpleNamespace(
            check_and_capture=_async_none
        )
        await _drive(service)
        assert not observability.counters().admission_refusals.get(
            "capture_lane_off"
        )


async def _async_none(**kwargs):
    return None


class TestTheOrderingIsRight:
    """M-C2b — the recorder must run BEFORE the early return."""

    @pytest.mark.asyncio
    async def test_the_recorder_runs_before_the_gate_returns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []

        def _spy(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(
            observability,
            "record_capture_lane_disabled_refusal",
            _spy,
            raising=True,
        )
        service = _service()
        result = await _drive(service, task_message="the canary prompt")

        assert result is None, "the gate must still refuse"
        assert calls, (
            "the recorder never ran — the gate returned BEFORE the refusal "
            "was counted (exactly the pre-fix ordering)"
        )
        assert len(calls) == 1
        assert calls[0]["agent_id"] == "agent-c2"
        assert calls[0]["prompt_source"] == "skill_metrics_service"
        assert calls[0]["task_message"] == "the canary prompt"

    @pytest.mark.asyncio
    async def test_the_raw_prompt_is_not_copied_into_the_record(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The shape discipline survives the new seam (D3/F7).

        The SINK record carries a prompt SHAPE; the raw prompt must not be
        written to it. (The helper receives the raw text — that is the
        documented contract — but the record must not carry it.)
        """
        canary = "CANARY-RAW-PROMPT-9f3a2b"
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path))
        service = _service()
        await _drive(service, task_message=canary)

        written = [
            path for path in tmp_path.rglob("*") if path.is_file()
        ]
        assert written, "the refusal sink record was never written"
        blob = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in written
        )
        assert "capture_lane_off" in blob
        assert canary not in blob, (
            "the raw prompt leaked into the sink record — only the SHAPE "
            "may be recorded"
        )


class TestTheSeamIsDefensive:
    """A broken/absent observability surface degrades, never breaks the gate."""

    @pytest.mark.asyncio
    async def test_a_raising_recorder_does_not_break_the_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(**kwargs):
            raise RuntimeError("simulated sink crash")

        monkeypatch.setattr(
            observability,
            "record_capture_lane_disabled_refusal",
            _boom,
            raising=True,
        )
        service = _service()
        assert await _drive(service) is None, (
            "the gate must still refuse when the sink raises — observability "
            "never breaks the capture path"
        )

    @pytest.mark.asyncio
    async def test_a_missing_symbol_degrades_to_the_previous_behaviour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The coupling is one-way: another lane owns the symbol."""
        monkeypatch.delattr(
            observability,
            "record_capture_lane_disabled_refusal",
            raising=True,
        )
        service = _service()
        assert await _drive(service) is None
        # …and nothing was counted (the pre-fix behaviour, silently degraded)
        assert not observability.counters().admission_refusals.get(
            "capture_lane_off"
        )


class TestTheHelperContract:
    def test_the_module_helper_is_the_single_seam(self) -> None:
        """The call site lives in ONE module-level helper, not inline."""
        import inspect

        from daemon.services import skill_metrics_service as module

        assert hasattr(module, "_record_capture_lane_off_refusal")
        source = inspect.getsource(
            SkillMetricsService._check_capture_eligibility
        )
        assert "_record_capture_lane_off_refusal(" in source
        # …and the observability import is INSIDE the guard (a module-level
        # import would couple the metrics lane's import graph to a file it
        # does not own, and an ImportError would break the daemon).
        helper_source = inspect.getsource(module._record_capture_lane_off_refusal)
        assert "import" in helper_source
        assert "getattr(" in helper_source
