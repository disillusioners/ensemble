"""D pins — capture-lane observability (phase1-plan §8; decisions.md DS-17/18/19).

Registered pack: ``skill_capture_observability_unit_test`` (NEW) — see
``.agents/tester/PACKS.md``.

Workstream D exists because the 2026-10-22 capture-lane deviation was
INVISIBLE: a refused ADMISSION mints no row at all, so a row-counting
monitor cannot see it, and nothing announced the deviation — the
incident only became visible through a forensic reconstruction.

WHAT IS OBSERVABLE TODAY vs WHAT PHASE 2 TURNS ON is documented in
``daemon/services/skill_capture_observability.py`` (the module docstring
carries the plan's table verbatim).

NAMED MUTATION LEGS (each must turn this file RED):

* **M-D1** — remove the refusal counter increment at one site → the
  "every refusal site increments" pin goes RED.
* **M-D2** — make the auto-anomaly trigger a no-op → the
  "the daemon announces the deviation" pin goes RED.
* **M-D3** — store the raw ``task_message`` in the new ``prompt_shape``
  field → the credential-hygiene pin goes RED (a raw prompt in a new
  field is RED).
* **M-D4** — merge the admission-refusal and execution-refusal signals
  into one counter → the "do not conflate" pin goes RED.

The kill-switches are NEVER mutated: the tests SIMULATE the switch by
patching the resolver function (``monkeypatch.setattr``), which is
scoped and restored — the host's env is never touched.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from daemon.services import skill_capture_observability as observability
from daemon.services import skill_job_dispatcher as dispatcher_module
from daemon.services.skill_job_dispatcher import (
    EVOLUTION_TYPE_FIX,
    PROMPT_SOURCE_COMPLETION_HOOK,
    SkillJobDispatcher,
)

# The fixtures of the A/B-resolution suite are imported explicitly (a
# pytest fixture is only visible to the module that defines it).
from tests.services.test_skill_evolution_service import (  # noqa: F401
    evolution_service,
    fake_embedding_service,
    fake_metrics_service,
    llm_config,
)

CANARY = "CANARY-RAW-PROMPT-TEXT-4f2b9c-do-not-store"


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path):
    """Point the sink at a scratch dir + reset the counters per test."""
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    observability.set_anomaly_reporter(None)
    try:
        yield tmp_path
    finally:
        observability.configure_sink(None)
        observability.reset_counters()
        observability.set_anomaly_reporter(None)


class _RecordingReporter:
    """Records the daemon-side anomaly calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"accepted": True}


class _FakeJobService:
    """Captures what the dispatcher admits (no DB)."""

    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    async def enqueue(self, **kwargs):
        self.enqueued.append(dict(kwargs))
        return SimpleNamespace(job_id="job-dpin-0001")


class _StubQueueRepo:
    """A queue repo whose parallel-queue lookup finds nothing (FIFO)."""

    def get_by_name(self, project_id, queue_name):
        return None


def _dispatcher(**kwargs) -> SkillJobDispatcher:
    """A dispatcher over fakes (no DB, no queue)."""
    defaults = dict(
        job_service=_FakeJobService(),
        queue_repo=_StubQueueRepo(),
        evolution_lane_enabled=True,
        fix_cooldown_seconds=0,
    )
    defaults.update(kwargs)
    return SkillJobDispatcher(**defaults)


# ── M-D1 — every refusal site increments ITS counter ─────────────────────


async def test_d_pin_every_refusal_site_increments_its_counter():
    """A refused admission counts; a refused execution counts separately."""
    snapshot = observability.counters().snapshot()
    assert "admission_refusals" in snapshot
    assert "execution_refusals" in snapshot

    # (a) the CAPTURE lane-off refusal (the capture switch defaults OFF).
    capture_dispatcher = _dispatcher()
    assert await capture_dispatcher.enqueue_capture(
        "proj-d", {"agent_id": "worker", "task_message": CANARY}
    ) is None
    assert observability.counters().admission_refusals.get(
        "capture_lane_off"
    ) == 1

    # (b) the EVOLUTION lane-off refusal (the lane is off in this
    # dispatcher — the switch itself is never mutated).
    lane_off_dispatcher = _dispatcher(evolution_lane_enabled=False)
    assert await lane_off_dispatcher.enqueue_evolution(
        "proj-d", "skill-dddd4444", EVOLUTION_TYPE_FIX, "fix it"
    ) is None
    assert observability.counters().admission_refusals.get(
        "evolution_lane_off"
    ) == 1

    # (c) the FIX cooldown refusal (RAM fast path).
    cooldown_dispatcher = _dispatcher(fix_cooldown_seconds=1800)
    await cooldown_dispatcher.enqueue_evolution(
        "proj-d", "skill-dddd4444", EVOLUTION_TYPE_FIX, "fix it"
    )
    assert await cooldown_dispatcher.enqueue_evolution(
        "proj-d", "skill-dddd4444", EVOLUTION_TYPE_FIX, "fix it"
    ) is None
    assert observability.counters().admission_refusals.get(
        "fix_cooldown"
    ) == 1

    # (d) the EXECUTION refusal (a terminal row WAS written).
    from daemon.repositories.job_queue.models import AdmissionState
    from daemon.services.job_processor import JobProcessor

    class _StubRepository:
        @staticmethod
        def atomic_transition(*args, **kwargs):
            return 1

    class _StubQueueService:
        _repository = _StubRepository()

    processor = JobProcessor.__new__(JobProcessor)
    processor._queue_service = _StubQueueService()
    refused = await processor._terminally_refuse_skill_evolution_job(
        SimpleNamespace(job_id="job-dpin-exec"),
        admission_state=AdmissionState.QUEUED.value,
        probe_reason="lane off",
    )
    assert refused is True
    assert observability.counters().execution_refusals.get(
        "lane_disabled"
    ) == 1


# ── M-D2 — the DAEMON announces the deviation (not the agent) ────────────


async def test_d_pin_deviation_is_announced_by_the_daemon(
    skill_repo, ab_test_repo, project_id, evolution_service, fake_metrics_service
):
    """A refused forced resolve is announced as an anomaly row."""
    from tests.services.test_skill_evolution_service import _make_skill

    reporter = _RecordingReporter()
    observability.set_anomaly_reporter(reporter)

    old = _make_skill(skill_repo, project_id, "old")
    new = _make_skill(skill_repo, project_id, "new")
    group = "g-d-deviation"
    ab_test_repo.create_ab_test(group, old.id, new.id)
    # comparisons stay 0 → below the sample size → the forced-resolve
    # guard refuses (the 2026-10-21 incident shape).
    fake_metrics_service.get_ab_comparison_stats.return_value = {
        "skill_id_a": old.id,
        "skill_id_b": new.id,
        "completion_rate_a": 0.5,
        "completion_rate_b": 0.5,
        "difference": 0.0,
        "comparisons": 0,
        "extension_count": 0,
        "ready_to_resolve": False,
        "needs_more_data": True,
    }

    with pytest.raises(ValueError):
        await evolution_service.check_ab_test_resolution(group, winner_id=new.id)

    assert len(reporter.calls) == 1, (
        "the daemon must ANNOUNCE the deviation — the Oct-22 keeper did "
        "not report itself, which is why the incident was invisible"
    )
    call = reporter.calls[0]
    assert call["category"] == observability.CATEGORY_LANE_DEVIATION
    assert call["category"] in observability.ANOMALY_CATEGORIES
    assert "deviation" in call["description"].lower()
    assert observability.counters().deviations == 1


# ── M-D3 — the new observability fields carry a SHAPE, never the prompt ──


async def test_d_pin_prompt_shape_never_carries_the_raw_prompt(
    monkeypatch, tmp_path
):
    """A raw prompt in the new fields is RED (the F7 credential lesson)."""
    # SIMULATE the capture switch ON (the env is never touched).
    monkeypatch.setattr(
        dispatcher_module, "_resolve_capture_enabled", lambda: True
    )

    dispatcher = _dispatcher()
    job_id = await dispatcher.enqueue_capture(
        "proj-d",
        {
            "agent_id": "worker",
            "task_message": CANARY,
            "iterations": 3,
        },
    )
    assert job_id is not None

    metadata = dispatcher._job_service.enqueued[0]["metadata"]
    shape = metadata["prompt_shape"]
    assert CANARY not in json.dumps(shape), (
        "the prompt_shape field must carry a SHAPE, never the raw prompt"
    )
    assert shape["length_bucket"] in ("short", "medium", "long")
    assert shape["has_agent_id"] is True
    assert shape["sha256_16"] and len(shape["sha256_16"]) == 16

    # The sink record must not carry the raw prompt either.
    sink_files = list((tmp_path / observability.SINK_DIR_NAME).glob("*.jsonl"))
    assert sink_files, "the sink must hold a record for the capture run"
    written = "".join(path.read_text() for path in sink_files)
    assert CANARY not in written
    assert "prompt_shape" in written


# ── M-D4 — the two refusal signals are NEVER merged ─────────────────────


async def test_d_pin_admission_and_execution_refusals_are_never_merged():
    """One counter for admissions, a DIFFERENT one for executions."""
    capture_dispatcher = _dispatcher()
    await capture_dispatcher.enqueue_capture(
        "proj-d", {"agent_id": "worker", "task_message": "hello"}
    )

    snapshot = observability.counters().snapshot()
    assert snapshot["admission_refusals"].get("capture_lane_off") == 1
    assert snapshot["execution_refusals"] == {}, (
        "an admission refusal must not touch the execution-refusal counter"
    )

    observability.record_execution_refusal("lane_disabled")
    snapshot = observability.counters().snapshot()
    assert snapshot["execution_refusals"].get("lane_disabled") == 1
    assert snapshot["admission_refusals"].get("capture_lane_off") == 1
    assert "lane_disabled" not in snapshot["admission_refusals"]
