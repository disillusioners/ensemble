"""E1 pins — the durable re-fix burst guard (phase1-plan §9; decisions.md DS-20).

Registered pack: ``skill_evolution_unit_test`` (EXTENDED) — see
``.agents/tester/PACKS.md``.

The incident this pins: skill ``20f916b3`` was re-FIXed 3× in 37.1
minutes on 2026-10-21 (21:26:09 → 21:49:02 → 22:03:13). The shipped
RAM cooldown (1800 s) covers the two observed gaps, but it DRAINS ON
RESTART and a sliding window still permits a slow trickle, and the
refusal was a single WARNING log line — no human-visible signal.

The guard therefore counts COMMITTED ``skill_evolution`` FIX
admissions for the SAME SKILL inside a window (default 3 per 24h) and
refuses the next one with "refused: burst cap" + an anomaly row.

⚠ THE COUNT IS KEYED ON THE SKILL IDENTITY, NOT ON THE JOB KEY.
``JobQueueService.enqueue`` suffixes a re-admission with ``#<uuid8>``
(``daemon/services/job_queue_service.py:1596-1608``) under a partial
UNIQUE index, so a count over ``idempotency_key`` matches AT MOST ONE
row ever and the cap could NEVER trip (council finding F-9). The fake
job service below reproduces that suffixing, so the pins are
non-vacuous against the dead-guard shape.

NAMED MUTATION LEGS (each must turn this file RED):

* **M-E1a** — key the count on ``idempotency_key`` instead of the
  skill id → the 4th admission is ADMITTED (the count returns to 1)
  and the cap never trips.
* **M-E1b** — remove the ``metadata->>'skill_id'`` filter → the count
  matches the wrong rows.
* **M-E1c** — drop the window filter (``created_at >= since``) → old
  admissions outside the window are counted.
* **M-E1d** — make the cap refuse the WRONG skill (ignore the skill id
  in the comparison) → a FIX admission for skill B is refused because
  skill A tripped the cap.
* **M-E1e** — remove the anomaly write on a cap refusal → the
  human-visible signal is gone.
* **M-E1f** — make the refusal envelope claim success → the refusal
  claims a job was admitted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.services import skill_capture_observability as observability
from daemon.services.skill_job_dispatcher import (
    EVOLUTION_TYPE_FIX,
    JOB_TYPE_EVOLUTION,
    SkillFixBurstCapRefused,
    SkillJobDispatcher,
)

SKILL_A = "skill-aaaa1111"
SKILL_B = "skill-bbbb2222"
PROJECT = "proj-burst-guard"


# ── Harness ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    """A file-backed SQLite engine with the job-queue tables created."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'burst-guard.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


class _FakeJobService:
    """A minimal job service that PERSISTS what the dispatcher admits.

    It mirrors the production ``JobQueueService.enqueue`` in the one
    respect that matters for this guard: a re-admission gets a UNIQUE
    synthetic idempotency key (``"<key>#<uuid8>"``,
    ``daemon/services/job_queue_service.py:1596-1608``). A count keyed
    on the job key therefore matches at most ONE row — the dead-guard
    shape this guard must not be.
    """

    def __init__(self, repo: JobRepository) -> None:
        self._repo = repo
        self.enqueued: list[dict] = []

    @property
    def repository(self) -> JobRepository:
        """The PUBLIC repository door the dispatcher resolves the ledger through."""
        return self._repo

    async def enqueue(self, **kwargs):
        metadata = kwargs.get("metadata") or {}
        idempotency_key = kwargs.get("idempotency_key")
        if idempotency_key:
            idempotency_key = f"{idempotency_key}#{uuid.uuid4().hex[:8]}"
        job = self._repo.create(
            agent_id=kwargs.get("agent_id", "skill-keeper"),
            agent_dir=kwargs.get("agent_dir", "agents/skill-keeper"),
            message=kwargs.get("message", ""),
            source=kwargs.get("source", "skill_evolution"),
            project_id=kwargs.get("project_id"),
            priority=kwargs.get("priority", 5),
            job_metadata=metadata,
            queue_id=kwargs.get("queue_id"),
            idempotency_key=idempotency_key,
            job_type=kwargs.get("job_type", "task"),
            instance_id=kwargs.get("instance_id"),
        )
        self.enqueued.append(
            {
                "job_id": job.job_id,
                "idempotency_key": idempotency_key,
                "metadata": dict(metadata),
                "job_type": job.job_type,
            }
        )
        return job


class _StubQueueRepo:
    """A queue repo whose parallel-queue lookup finds nothing (FIFO)."""

    def get_by_name(self, project_id, queue_name):
        return None


class _RecordingReporter:
    """Records the daemon-side anomaly calls (the human-visible signal)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"accepted": True}


def _make_dispatcher(engine, reporter=None, *, cap=3, window=86400):
    """Build a dispatcher over a real repository + a fake job service."""
    repo = JobRepository(engine)
    job_service = _FakeJobService(repo)
    dispatcher = SkillJobDispatcher(
        job_service=job_service,
        queue_repo=_StubQueueRepo(),
        evolution_lane_enabled=True,
        # The RAM cooldown is DISABLED here on purpose: these pins
        # exercise the DURABLE cap alone (acceptance #2 — the refusal
        # must survive a restart, which the RAM cooldown cannot).
        fix_cooldown_seconds=0,
        fix_burst_cap=cap,
        fix_burst_window_seconds=window,
        anomaly_reporter=reporter,
    )
    return dispatcher, job_service, repo


async def _admit(dispatcher, skill_id, direction="fix it"):
    """Admit one FIX job for ``skill_id`` (returns the job id)."""
    return await dispatcher.enqueue_evolution(
        PROJECT, skill_id, EVOLUTION_TYPE_FIX, direction
    )


def _count(repo, skill_id, *, hours_ago=24):
    since = (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ).isoformat()
    return repo.count_fix_admissions_since(skill_id, since)


# ── Acceptance #1 — the cap TRIPS on the 4th admission ───────────────────


@pytest.mark.asyncio
async def test_e1_cap_trips_on_the_fourth_fix_admission(engine):
    """The 4th FIX admission for the same skill is refused, loudly."""
    observability.reset_counters()
    reporter = _RecordingReporter()
    dispatcher, job_service, repo = _make_dispatcher(engine, reporter)

    for _ in range(3):
        job_id = await _admit(dispatcher, SKILL_A)
        assert job_id, "the first three admissions must be admitted"

    with pytest.raises(SkillFixBurstCapRefused) as excinfo:
        await _admit(dispatcher, SKILL_A)

    message = str(excinfo.value)
    assert "refused: burst cap" in message, (
        f"the refusal must name the burst cap — got {message!r}"
    )

    # No 4th row was minted (a refused ADMISSION writes NO row).
    assert _count(repo, SKILL_A) == 3
    assert len(job_service.enqueued) == 3

    # The human-visible signal: an anomaly row + a refusal counter.
    assert len(reporter.calls) == 1, "the cap refusal must write ONE anomaly row"
    assert reporter.calls[0]["category"] == observability.CATEGORY_BURST_CAP
    assert "refused: burst cap" in reporter.calls[0]["description"]
    assert observability.counters().admission_refusals.get(
        "fix_burst_cap"
    ) == 1


# ── Acceptance #2 — the refusal is DURABLE across a restart ───────────────


@pytest.mark.asyncio
async def test_e1_refusal_survives_a_dispatcher_restart(engine):
    """A NEW dispatcher (RAM empty) still refuses the 4th admission."""
    observability.reset_counters()
    reporter = _RecordingReporter()

    first, first_jobs, _repo = _make_dispatcher(engine, reporter)
    for _ in range(3):
        await _admit(first, SKILL_A)

    # "Restart": a brand-new dispatcher over the SAME database — the
    # RAM cooldown map is empty, so only the DURABLE count can refuse.
    second, second_jobs, _repo2 = _make_dispatcher(engine, reporter)

    with pytest.raises(SkillFixBurstCapRefused):
        await _admit(second, SKILL_A)

    assert len(second_jobs.enqueued) == 0
    assert len(first_jobs.enqueued) == 3


# ── Acceptance #3 — different skills are INDEPENDENT (M-E1d) ──────────────


@pytest.mark.asyncio
async def test_e1_different_skills_are_independent(engine):
    """Skill B is NOT refused because skill A tripped the cap."""
    observability.reset_counters()
    reporter = _RecordingReporter()
    dispatcher, _jobs, _repo = _make_dispatcher(engine, reporter)

    for _ in range(3):
        await _admit(dispatcher, SKILL_A)

    with pytest.raises(SkillFixBurstCapRefused):
        await _admit(dispatcher, SKILL_A)

    # Skill B has its own count — it must be admitted.
    job_id = await _admit(dispatcher, SKILL_B)
    assert job_id, "skill B must be admitted — the cap is per skill"


# ── Acceptance #4 — the count is keyed on the SKILL IDENTITY ──────────────


@pytest.mark.asyncio
async def test_e1_count_is_keyed_on_skill_identity_not_the_job_key(engine):
    """The admitted rows carry DISTINCT job keys; the count ignores them."""
    observability.reset_counters()
    dispatcher, job_service, repo = _make_dispatcher(engine)

    await _admit(dispatcher, SKILL_A)
    await _admit(dispatcher, SKILL_A)
    await _admit(dispatcher, SKILL_B)

    keys = [entry["idempotency_key"] for entry in job_service.enqueued]
    assert len(set(keys)) == 3, (
        "the re-admissions must carry distinct job keys (the #<uuid8> "
        "suffix) — otherwise this pin would not exercise the dead-guard "
        "shape"
    )
    assert _count(repo, SKILL_A) == 2
    assert _count(repo, SKILL_B) == 1


# ── M-E1b — the skill-id filter matches the RIGHT rows ────────────────────


@pytest.mark.asyncio
async def test_e1_count_only_matches_the_requested_skill(engine):
    """Rows for other skills (and other job types) are not counted."""
    observability.reset_counters()
    dispatcher, _jobs, repo = _make_dispatcher(engine)

    await _admit(dispatcher, SKILL_A)
    await _admit(dispatcher, SKILL_B)

    assert _count(repo, SKILL_A) == 1
    assert _count(repo, SKILL_B) == 1
    assert _count(repo, "skill-zzzz9999") == 0


# ── M-E1c — the window filter excludes OLD admissions ─────────────────────


@pytest.mark.asyncio
async def test_e1_old_admissions_outside_the_window_are_not_counted(engine):
    """An admission from 48h ago must not count against a 24h window."""
    observability.reset_counters()
    dispatcher, job_service, repo = _make_dispatcher(engine)

    await _admit(dispatcher, SKILL_A)

    # Age the committed row beyond the window (the durable count reads
    # COMMITTED rows — the row's own timestamp is the window anchor).
    aged = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE job_queue_items SET created_at = ?", (aged,)
        )

    assert _count(repo, SKILL_A, hours_ago=24) == 0

    # ...and the admission is therefore admitted (the cap does not trip).
    job_id = await _admit(dispatcher, SKILL_A)
    assert job_id, "an admission outside the window must not trip the cap"


# ── M-E1e — the anomaly write is load-bearing ────────────────────────────


@pytest.mark.asyncio
async def test_e1_cap_refusal_writes_an_anomaly_row(engine):
    """The refusal is announced through the daemon-side accept path."""
    observability.reset_counters()
    reporter = _RecordingReporter()
    dispatcher, _jobs, _repo = _make_dispatcher(engine, reporter)

    for _ in range(3):
        await _admit(dispatcher, SKILL_A)

    with pytest.raises(SkillFixBurstCapRefused):
        await _admit(dispatcher, SKILL_A)

    assert len(reporter.calls) == 1
    call = reporter.calls[0]
    assert call["category"] in observability.ANOMALY_CATEGORIES
    assert call["agent_id"] == observability.SKILL_KEEPER_AGENT_ID
    assert call["instance_id"]
    assert call["severity"] in ("minor", "major", "critical")
    assert "refused: burst cap" in call["description"]


# ── M-E1f — the refusal envelope NEVER claims success ────────────────────


@pytest.mark.asyncio
async def test_e1_refusal_never_claims_a_job_was_admitted(engine):
    """The refusal says what happened — it never claims a dispatched job."""
    observability.reset_counters()
    dispatcher, job_service, _repo = _make_dispatcher(engine)

    for _ in range(3):
        await _admit(dispatcher, SKILL_A)

    with pytest.raises(SkillFixBurstCapRefused) as excinfo:
        await _admit(dispatcher, SKILL_A)

    message = str(excinfo.value)
    assert "refused: burst cap" in message
    for claim in ("admitted", "dispatched", "job created"):
        assert claim not in message.lower(), (
            f"the refusal envelope must not claim success ({claim!r} in "
            f"{message!r})"
        )
    assert len(job_service.enqueued) == 3, (
        "a refused admission must not create a job row"
    )


# ── The repository literals must not drift from the dispatcher constants ──


def test_e1_repository_literals_match_the_dispatcher_constants():
    """The repository's job-type / evolution-type literals are pinned."""
    from daemon.repositories.job_queue import repository as repo_module

    assert repo_module._JOB_TYPE_EVOLUTION == JOB_TYPE_EVOLUTION
    assert repo_module._EVOLUTION_TYPE_FIX == EVOLUTION_TYPE_FIX
