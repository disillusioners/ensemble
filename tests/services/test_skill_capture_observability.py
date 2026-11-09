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


# ══════════════════════════════════════════════════════════════════════
# FIX WAVE 1 — the C2 seam, the m2/m3/m4 corrections and the C1 chain
# ══════════════════════════════════════════════════════════════════════
#
# NAMED MUTATION LEGS (each must turn this file RED):
#
# * **M-C2** — make ``record_capture_lane_disabled_refusal`` a no-op
#   (or drop the counter increment) → the "the metrics lane's seam
#   counts AND records" pin goes RED.
# * **M-m2a** — stop passing ``deviation=True`` at the D3 producer →
#   the "the sink carries the deviation flag" pin goes RED.
# * **M-m2b** — mint the run id AFTER the admission gate (the pre-fix
#   shape) → the "a refused admission still carries a run id" pin goes
#   RED.
# * **M-m3** — count the admission on a dedup hit (the pre-fix shape)
#   → the "a dedup hit does not move the gauge" pin goes RED.
# * **M-m4** — remove/break the public counters accessor → the "the
#   public read surface returns the counters" pin goes RED.
# * **M-C1** — break ONE link of the production holder chain (the
#   metrics service → dispatcher → job service → instance manager →
#   anomaly service) → the "the anomaly service resolves through the
#   REAL chain" pin goes RED (it asserted a MODULE-level double, so
#   the false positive it replaces survived a green matrix).


# ── M-C2 — the metrics lane's ready-to-serve seam ─────────────────────


def test_c2_pin_capture_lane_disabled_refusal_counts_and_records(tmp_path):
    """M-C2 — the seam the METRICS lane calls must count AND record.

    The production capture refusal lives in the metrics lane
    (``skill_metrics_service.py:595-596``), which this lane does not
    own; this helper is the ready-to-serve counterpart. It must
    increment the ADMISSION-refusal counter AND write the sink record
    — with the prompt SHAPE only, never the raw prompt.

    Named mutation: make the helper a no-op (or drop the counter
    increment) → this pin goes RED.
    """
    observability.record_capture_lane_disabled_refusal(
        agent_id="worker",
        task_message=CANARY,
        prompt_source=PROMPT_SOURCE_COMPLETION_HOOK,
        run_id=observability.new_run_id(),
    )

    assert observability.counters().admission_refusals.get(
        "capture_lane_off"
    ) == 1, (
        "the capture-lane-off refusal must increment the ADMISSION "
        "counter — the metrics lane's seam is the only production "
        "path that can move it"
    )

    sink_files = list((tmp_path / observability.SINK_DIR_NAME).glob("*.jsonl"))
    assert sink_files, "the seam must write a sink record"
    written = "".join(path.read_text() for path in sink_files)
    assert CANARY not in written, (
        "the sink record must carry the prompt SHAPE, never the raw "
        "prompt (DS-18 / the F7 credential lesson)"
    )
    record = json.loads(written.strip().splitlines()[-1])
    assert record["refusal_reason"] == "capture_lane_off"
    assert record["outcome"] == "refused"
    assert record["stage"] == "admission"
    assert record["prompt_shape"]["length_bucket"] in (
        "short", "medium", "long",
    )


def test_c2_pin_capture_lane_disabled_refusal_never_raises():
    """M-C2 — the seam must NEVER break the capture path.

    Named mutation: let the helper propagate an exception (e.g. by
    removing its try/except) → this pin goes RED.
    """
    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("boom")

    # A hostile payload must not turn the seam into a failure.
    observability.record_capture_lane_disabled_refusal(
        agent_id=_Boom(),
        task_message=_Boom(),
        prompt_source=_Boom(),
        run_id=_Boom(),
    )
    assert observability.counters().admission_refusals.get(
        "capture_lane_off"
    ) == 1


# ── M-m4 — the public READ surface for the counters ───────────────────


def test_m4_pin_public_counters_accessor_returns_the_counters():
    """M-m4 — a PUBLIC accessor must expose the counters.

    Named mutation: make ``counters_snapshot`` return ``{}`` (or
    remove it) → this pin goes RED. The metrics-registry READ SITE
    (the metrics lane) calls this accessor — this module owns the
    VALUE, the metrics lane owns the read site.

    Named mutation: make the accessor return the LIVE counter object
    instead of a JSON-safe snapshot → the "never the live object"
    assertion below goes RED.
    """
    observability.record_admission_refusal("capture_lane_off")
    observability.record_admission_refusal("capture_lane_off")
    observability.record_deviation()

    snapshot = observability.counters_snapshot()

    assert snapshot["admission_refusals"].get("capture_lane_off") == 2
    assert snapshot["deviations"] == 1
    assert isinstance(snapshot, dict)
    # The accessor must hand out a SNAPSHOT — a caller that mutated it
    # must not be able to move the counters.
    snapshot["admission_refusals"]["capture_lane_off"] = 999
    assert observability.counters().admission_refusals.get(
        "capture_lane_off"
    ) == 2, (
        "the public accessor must return a COPY — a caller mutated the "
        "live counters"
    )


# ── M-m2 — the deviation flag + the refused-admission run id ──────────


async def test_m2_pin_deviation_sink_record_carries_the_deviation_flag(
    skill_repo, ab_test_repo, project_id, evolution_service,
    fake_metrics_service, tmp_path,
):
    """M-m2 — the SINK record must carry ``deviation=true``.

    Named mutation: stop passing ``deviation=True`` at the D3
    producer (the pre-fix shape — the flag was declared but never set
    by any producer) → this pin goes RED.

    Named mutation: make the D3 producer skip the sink record → this
    pin goes RED.
    """
    from tests.services.test_skill_evolution_service import _make_skill

    old = _make_skill(skill_repo, project_id, "old")
    new = _make_skill(skill_repo, project_id, "new")
    group = "g-m2-deviation"
    ab_test_repo.create_ab_test(group, old.id, new.id)
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

    sink_files = list((tmp_path / observability.SINK_DIR_NAME).glob("*.jsonl"))
    assert sink_files, "the D3 producer must write a sink record"
    records = [
        json.loads(line)
        for line in "".join(p.read_text() for p in sink_files)
        .strip()
        .splitlines()
    ]
    deviations = [r for r in records if r.get("deviation")]
    assert deviations, (
        "no sink record carries deviation=true — the D3 producer writes "
        "no deviation record, so the sink's deviation flag was dead"
    )
    assert deviations[0]["stage"] == "deviation"
    assert deviations[0]["refusal_reason"] == "forced_resolve_below_sample_size"
    # ids + counts only — never a prompt.
    assert "task_message" not in json.dumps(deviations[0])


async def test_m2_pin_refused_admission_records_carry_a_run_id(tmp_path):
    """M-m2 — a REFUSED admission must still carry a run id.

    Named mutation: mint the run id AFTER the admission gate (the
    pre-fix shape — the id was minted after the kill-switch check) →
    this pin goes RED.
    """
    dispatcher = _dispatcher()

    # (a) the capture lane-off refusal.
    assert await dispatcher.enqueue_capture(
        "proj-d", {"agent_id": "worker", "task_message": CANARY}
    ) is None

    # (b) the evolution lane-off refusal.
    lane_off = _dispatcher(evolution_lane_enabled=False)
    assert await lane_off.enqueue_evolution(
        "proj-d", "skill-dddd4444", EVOLUTION_TYPE_FIX, "fix it"
    ) is None

    sink_files = list((tmp_path / observability.SINK_DIR_NAME).glob("*.jsonl"))
    assert sink_files
    records = [
        json.loads(line)
        for line in "".join(p.read_text() for p in sink_files)
        .strip()
        .splitlines()
    ]
    refusals = [r for r in records if r.get("outcome") == "refused"]
    assert len(refusals) == 2, (
        f"expected the two refusals in the sink, got {len(refusals)}"
    )
    for record in refusals:
        assert record.get("run_id"), (
            "a refused admission must carry a run id — the run id is "
            "minted BEFORE the gate, so a refusal is still correlatable"
        )


# ── M-m3 — a dedup hit must NOT move the admission gauge ──────────────


class _DedupJobRepo:
    """A job repository that already holds the idempotency key."""

    def __init__(self) -> None:
        self.jobs: dict[str, object] = {}

    def find_by_idempotency_key(self, idempotency_key):
        return self.jobs.get(idempotency_key)


class _DedupJobService:
    """A job service whose enqueue COLLAPSES onto the existing job.

    This mirrors the production dedup: the atomic
    ``create_or_get_by_idempotency_key`` returns the PRE-EXISTING row
    when the key is already held by a non-terminal job — no NEW row is
    minted.
    """

    def __init__(self) -> None:
        self.repository = _DedupJobRepo()
        self.enqueued: list[dict] = []

    async def enqueue(self, **kwargs):
        self.enqueued.append(dict(kwargs))
        return self.repository.find_by_idempotency_key(
            kwargs.get("idempotency_key")
        )


async def test_m3_pin_dedup_hit_does_not_increment_the_fix_gauge():
    """M-m3 — a dedup hit mints NO row, so the gauge must not move.

    Named mutation: count the admission unconditionally (the pre-fix
    shape — the gauge counted a dedup hit as a new FIX admission) →
    this pin goes RED.
    """
    job_service = _DedupJobService()
    key = "skill_fix:proj-m3:skill-m3"
    job_service.repository.jobs[key] = SimpleNamespace(job_id="job-dedup-0001")

    dispatcher = SkillJobDispatcher(
        job_service=job_service,
        queue_repo=_StubQueueRepo(),
        evolution_lane_enabled=True,
        fix_cooldown_seconds=0,
    )

    before = observability.counters().snapshot()
    job_id = await dispatcher.enqueue_evolution(
        "proj-m3", "skill-m3", EVOLUTION_TYPE_FIX, "fix it"
    )

    assert job_id == "job-dedup-0001", (
        "the dedup hit must return the pre-existing job id"
    )
    after = observability.counters().snapshot()
    assert after["fix_admissions"] == before["fix_admissions"], (
        "a dedup hit minted NO new row, so the fix-admission gauge must "
        f"not move (before={before['fix_admissions']}, "
        f"after={after['fix_admissions']}) — the gauge over-counts "
        "relative to the durable DB count"
    )


async def test_m3_pin_a_fresh_admission_still_moves_the_fix_gauge():
    """M-m3 — the counter must still move for a REAL admission.

    Named mutation: gate the counter so it NEVER moves (the
    over-correction of the m3 fix) → this pin goes RED.
    """
    dispatcher = _dispatcher()

    before = observability.counters().snapshot()
    job_id = await dispatcher.enqueue_evolution(
        "proj-m3-fresh", "skill-m3-fresh", EVOLUTION_TYPE_FIX, "fix it"
    )

    assert job_id is not None
    after = observability.counters().snapshot()
    assert after["fix_admissions"] == before["fix_admissions"] + 1, (
        "a FRESH FIX admission must increment the gauge"
    )


# ── M-C1 — the anomaly service resolves through the REAL chain ────────


def _real_anomaly_service(tmp_path):
    """A REAL AnomalyService over a scratch SQLite engine."""
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel

    from daemon.config import NestReportConfig
    from daemon.repositories.anomaly.models import (
        AnomalyCluster,
        AnomalyReport,
    )
    from daemon.services.anomaly_service import AnomalyService

    async def _passthrough(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'anomaly-chain.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
    )
    service = AnomalyService(
        engine,
        config=NestReportConfig(),
        offload=_passthrough,
    )
    return service, engine


async def test_c1_pin_anomaly_service_resolves_through_the_production_chain(
    tmp_path,
):
    """M-C1 — the D3 trigger must reach the REAL anomaly service.

    The chain is the PRODUCTION one: metrics service
    (``set_job_dispatcher``) → ``_skill_job_dispatcher`` → the
    dispatcher's ``_job_service`` → ``_instance_manager`` →
    ``anomaly_service``. NO reporter is injected — the resolution must
    happen through the holder chain alone (the pre-fix pin injected a
    reporter double, which is why a "dead in production" false
    positive survived a green matrix).

    Named mutation: break ONE link of the chain (e.g. rename the
    dispatcher's ``_job_service`` attribute, or drop the
    ``set_job_dispatcher`` wiring) → this pin goes RED.
    """
    from daemon.repositories.anomaly.models import AnomalyReport
    from daemon.services.skill_metrics_service import SkillMetricsService
    from sqlmodel import Session, select

    anomaly_service, engine = _real_anomaly_service(tmp_path)

    # The production holder chain, attribute for attribute.
    instance_manager = SimpleNamespace(anomaly_service=anomaly_service)
    job_service = SimpleNamespace(_instance_manager=instance_manager)
    dispatcher = SkillJobDispatcher(
        job_service=job_service,
        queue_repo=_StubQueueRepo(),
        evolution_lane_enabled=True,
    )

    metrics = SkillMetricsService(
        usage_repo=object(),
        skill_repo=object(),
        trigger_repo=object(),
        ab_test_repo=object(),
        config=SimpleNamespace(),
    )
    metrics.set_job_dispatcher(dispatcher)

    # NO injected reporter — the resolution must walk the chain.
    observability.set_anomaly_reporter(None)

    resolved = observability.resolve_anomaly_service(metrics)
    assert resolved is anomaly_service, (
        "the anomaly service must resolve through the production "
        "holder chain (metrics service → dispatcher → job service → "
        "instance manager → anomaly service)"
    )

    # And the D3 trigger must announce THROUGH it.
    announced = await observability.emit_lane_anomaly(
        category=observability.CATEGORY_LANE_DEVIATION,
        description="Keeper deviation: forced A/B resolve refused "
        "for ab_test_group='g-c1' (comparisons=0 < ab_sample_size=10).",
        severity="major",
        holder=metrics,
    )
    assert announced is True, (
        "the D3 trigger must announce through the resolved anomaly "
        "service — with no injected reporter, the resolution must come "
        "from the production holder chain"
    )

    with Session(engine) as session:
        rows = list(session.exec(select(AnomalyReport)).all())
    assert rows, "the anomaly service must have persisted the report"
    assert "Keeper deviation" in rows[-1].description
