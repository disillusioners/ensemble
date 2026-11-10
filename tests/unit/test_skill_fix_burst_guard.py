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

import ast
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path):
    """Point the sink at a SCRATCH dir + reset the counters per test.

    L7 — without this fixture the burst-guard admissions wrote their
    observability rows into the CHECKOUT's real ``data/`` tree
    (``data/skill-capture-observability/skill-capture-<date>.jsonl``).
    The tree is gitignored, so the leak was invisible to ``git status``
    and survived every run. The fixture is copied verbatim from
    ``tests/services/test_skill_capture_observability.py``.
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


# ── M11 — the refusal envelope has ONE shape (base + class token) ────────


class _CapRefusingDispatcher:
    """A dispatcher double whose ``dispatch_fix`` raises the REAL refusal."""

    async def dispatch_fix(self, **kwargs):
        raise SkillFixBurstCapRefused(
            "refused: burst cap — 3 FIX admissions for skill abc12345 "
            "within 86400s (cap 3). NO skill-keeper job was created."
        )


def _route_refusal_reason_node():
    """The AST node the router uses for the refusal envelope's ``reason``.

    Returns ``(handler_name, value_node)`` for the ``reason`` key inside
    the ``SkillFixBurstCapRefused`` handler, or ``(None, None)``.
    """
    import ast

    source = (
        Path(__file__).resolve().parents[2]
        / "daemon"
        / "routers"
        / "skills.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        exc = node.type
        names: list[str] = []
        if isinstance(exc, ast.Name):
            names = [exc.id]
        elif isinstance(exc, ast.Tuple):
            names = [
                element.id for element in exc.elts if isinstance(element, ast.Name)
            ]
        if "SkillFixBurstCapRefused" not in names:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Dict):
                continue
            for key, value in zip(sub.keys, sub.values):
                if isinstance(key, ast.Constant) and key.value == "reason":
                    return node.name, value
    return None, None


def test_m11_the_burst_cap_refusal_shares_the_shared_base():
    """Named mutation: make ``SkillFixBurstCapRefused`` inherit ``RuntimeError``
    again (drop the shared base) → this pin goes RED.
    """
    from daemon.services.skill_lane_refusal import SkillLaneRefused

    assert issubclass(SkillFixBurstCapRefused, SkillLaneRefused), (
        "SkillFixBurstCapRefused must share the SkillLaneRefused base — "
        "the skill lane has ONE refusal envelope shape"
    )


def test_m11_the_burst_cap_refusal_declares_its_token_on_the_class():
    """Named mutation: delete the class-level ``reason`` → this pin goes RED."""
    token = getattr(SkillFixBurstCapRefused, "reason", None)
    assert isinstance(token, str) and token == "fix_burst_cap", (
        "the burst-cap refusal must declare its stable token as a CLASS "
        "attribute — the route reads it, so the two cannot drift"
    )


def test_m11_the_burst_cap_refusal_keeps_its_message_prefix():
    """The agent-facing message prefix is EXACTLY preserved."""
    refusal = SkillFixBurstCapRefused(
        "refused: burst cap — 3 FIX admissions for skill abc12345 "
        "within 86400s (cap 3). NO skill-keeper job was created."
    )
    assert str(refusal).startswith("refused: burst cap")
    assert refusal.reason == "fix_burst_cap"
    assert refusal.detail == str(refusal)


def test_m11_the_route_reads_the_reason_from_the_refusal_class():
    """Named mutation: hard-code ``"reason": "fix_burst_cap"`` back into the
    router → this pin goes RED (the route would carry a literal, and a
    token re-diverge would go unnoticed).
    """
    handler_name, value = _route_refusal_reason_node()
    assert handler_name is not None, (
        "the skills router no longer has a SkillFixBurstCapRefused handler"
    )
    assert not isinstance(value, ast.Constant), (
        "the route HARD-CODES the refusal reason — it must READ e.reason "
        "(one token per concept; the route cannot drift from the class)"
    )
    assert isinstance(value, ast.Attribute) and value.attr == "reason", (
        "the route must read the 'reason' ATTRIBUTE off the refusal "
        f"(got {type(value).__name__})"
    )
    assert isinstance(value.value, ast.Name) and value.value.id == handler_name, (
        "the route must read the reason off the exception it caught"
    )


def test_m11_the_route_envelope_carries_the_class_token():
    """The route's answer must carry the CLASS's token, not a look-alike."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from daemon.routers.skills import _require_skill_dispatcher
    from daemon.routers.skills import router as skills_router

    app = FastAPI()
    app.include_router(skills_router, prefix="/api")
    app.dependency_overrides[_require_skill_dispatcher] = (
        lambda: _CapRefusingDispatcher()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/skills/skill-abc12345/fix",
            json={"issue_description": "the skill misleads the agent"},
        )

    assert response.status_code == 429, (
        f"the burst-cap refusal must be a truthful 429 — got "
        f"{response.status_code}: {response.text[:400]}"
    )
    detail = response.json().get("detail", {})
    assert detail.get("reason") == SkillFixBurstCapRefused.reason, (
        "the refusal envelope's reason must be the CLASS's token "
        f"(got {detail.get('reason')!r}, expected "
        f"{SkillFixBurstCapRefused.reason!r})"
    )


# ── M19 — a negative burst knob is REJECTED at load, never clamped ──────


def _skill_evolution_config(**overrides):
    """Build a ``SkillEvolutionConfig`` with the env lane scrubbed."""
    from daemon.config import SkillEvolutionConfig

    return SkillEvolutionConfig(**overrides)


def test_m19_a_negative_burst_cap_is_rejected_loudly_at_load(monkeypatch):
    """A NEGATIVE cap must be a loud config error, never a silent 0.

    A negative cap was clamped to 0 by the dispatcher — and 0 DISABLES
    the durable guard, so a typo DISARMED the burst cap without a word.

    Named mutation: drop the ``ge=0`` from ``fix_burst_cap`` → this pin
    goes RED (the negative value is accepted).
    """
    from pydantic import ValidationError

    monkeypatch.setenv("SKILL_EVOLUTION_FIX_BURST_CAP", "-1")
    with pytest.raises(ValidationError):
        _skill_evolution_config()

    monkeypatch.delenv("SKILL_EVOLUTION_FIX_BURST_CAP", raising=False)
    with pytest.raises(ValidationError):
        _skill_evolution_config(fix_burst_cap=-1)


def test_m19_a_non_positive_burst_window_is_rejected_loudly_at_load(monkeypatch):
    """A zero/negative window must be a loud config error.

    Named mutation: drop the ``ge=1`` from ``fix_burst_window_seconds``
    → this pin goes RED (a zero window is accepted).
    """
    from pydantic import ValidationError

    monkeypatch.setenv("SKILL_EVOLUTION_FIX_BURST_WINDOW_SECONDS", "0")
    with pytest.raises(ValidationError):
        _skill_evolution_config()

    monkeypatch.delenv(
        "SKILL_EVOLUTION_FIX_BURST_WINDOW_SECONDS", raising=False
    )
    with pytest.raises(ValidationError):
        _skill_evolution_config(fix_burst_window_seconds=0)


def test_m19_the_explicit_zero_cap_still_survives(monkeypatch):
    """The documented "0 disables the cap" semantics MUST NOT break.

    Named mutation: change ``ge=0`` to ``ge=1`` → this pin goes RED (an
    explicit 0 — the documented disable — is rejected at load).
    """
    monkeypatch.delenv("SKILL_EVOLUTION_FIX_BURST_CAP", raising=False)
    config = _skill_evolution_config(fix_burst_cap=0)
    assert config.fix_burst_cap == 0, (
        "an explicit 0 must SURVIVE the config load — 0 is the documented "
        f"'disable the cap' value (got {config.fix_burst_cap!r})"
    )

    monkeypatch.setenv("SKILL_EVOLUTION_FIX_BURST_CAP", "0")
    config = _skill_evolution_config()
    assert config.fix_burst_cap == 0, (
        "an explicit 0 via the env alias must SURVIVE the config load"
    )


def test_m19_a_directly_constructed_negative_cap_is_logged(caplog):
    """The dispatcher's clamp must never disarm the cap SILENTLY.

    Named mutation: delete the WARNING in the dispatcher constructor →
    this pin goes RED (the clamp is silent again).
    """
    dispatcher = SkillJobDispatcher(
        job_service=object(),
        queue_repo=object(),
        evolution_lane_enabled=True,
        fix_cooldown_seconds=0,
        fix_burst_cap=-1,
    )
    assert dispatcher._fix_burst_cap == 0
    warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "fix_burst_cap" in record.getMessage()
    ]
    assert warnings, (
        "a negative fix_burst_cap clamped to 0 (which DISABLES the durable "
        "burst cap) must be logged at WARNING naming the raw value"
    )
    assert "-1" in warnings[0].getMessage()


# ── L7 — the burst-guard suite writes NOTHING into the checkout's data/ ──


@pytest.mark.asyncio
async def test_l7_the_burst_guard_writes_nothing_under_the_real_data_dir(
    engine, tmp_path
):
    """The observability sink must be redirected to a SCRATCH dir.

    The burst-guard pins used to write their observability rows into the
    CHECKOUT's real ``data/skill-capture-observability/`` tree. The tree
    is gitignored, so the leak was invisible to ``git status`` and
    survived every run.

    Named mutation: delete the autouse ``_observability_sandbox``
    fixture (the ``configure_sink(tmp_path)`` call) → this pin goes RED
    (a test row lands in the real data dir).

    The pin cleans up after itself: a stray file it created is removed
    in the ``finally`` block, so even a RED run leaves the checkout
    clean.
    """
    real_dir = (
        Path(__file__).resolve().parents[2]
        / "data"
        / observability.SINK_DIR_NAME
    )
    before = set(real_dir.glob("*.jsonl")) if real_dir.exists() else set()
    try:
        # A lane-OFF admission is REFUSED, and a refusal always writes a
        # sink record — so the scratch sink is guaranteed to receive a
        # row (the ADMITTED path writes none).
        repo = JobRepository(engine)
        dispatcher = SkillJobDispatcher(
            job_service=_FakeJobService(repo),
            queue_repo=_StubQueueRepo(),
            evolution_lane_enabled=False,
            fix_cooldown_seconds=0,
        )
        await _admit(dispatcher, SKILL_A)

        after = set(real_dir.glob("*.jsonl")) if real_dir.exists() else set()
        stray = sorted(str(path) for path in (after - before))
        assert not stray, (
            "the burst-guard pins wrote into the checkout's REAL data dir "
            f"({observability.SINK_DIR_NAME}/): {stray} — the autouse "
            "_observability_sandbox fixture (configure_sink(tmp_path)) is "
            "missing, so the sink resolved to ./data"
        )

        scratch = tmp_path / observability.SINK_DIR_NAME
        scratch_files = list(scratch.glob("*.jsonl"))
        assert scratch_files, (
            "the admission must have written its observability rows into "
            f"the SCRATCH sink dir ({scratch}) — the fixture did not run"
        )
        assert any(
            SKILL_A in path.read_text(encoding="utf-8")
            for path in scratch_files
        ), "the scratch sink must carry the admission's row"
    finally:
        if real_dir.exists():
            for stray_path in set(real_dir.glob("*.jsonl")) - before:
                stray_path.unlink(missing_ok=True)


# ── M18 — every DEGRADED burst-cap branch is ALWAYS RECORDED ────────────


class _LedgerlessJobService:
    """A job service that exposes NO ledger at all."""

    async def enqueue(self, **kwargs):  # pragma: no cover — never reached
        raise AssertionError("no job may be admitted in this pin")


class _LedgerlessQueueRepo:
    """A queue repo without the durable-count accessor."""

    def get_by_name(self, project_id, queue_name):
        return None


def _sink_records(tmp_path):
    """Every observability row written into the SCRATCH sink."""
    sink_dir = tmp_path / observability.SINK_DIR_NAME
    records = []
    for path in sorted(sink_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


@pytest.mark.asyncio
async def test_m18_the_absent_ledger_degradation_is_always_recorded(tmp_path):
    """The PERMANENT "no ledger" degradation must leave a RECORD.

    The operator sees "the cap is enabled" at boot while it is inert; the
    posture stays fail-open for AVAILABILITY, but it is ALWAYS recorded.

    Named mutation: remove the ``append_run_record`` from the
    "no FIX-admission ledger" branch → this pin goes RED.
    """
    dispatcher = SkillJobDispatcher(
        job_service=_LedgerlessJobService(),
        queue_repo=_LedgerlessQueueRepo(),
        evolution_lane_enabled=True,
        fix_cooldown_seconds=0,
    )

    refusal = await dispatcher._check_fix_burst_cap(
        SKILL_A, run_id="run-degraded-ledger"
    )
    assert refusal is None, (
        "the degraded posture stays FAIL-OPEN for availability — the "
        "admission proceeds"
    )

    degraded = [
        record
        for record in _sink_records(tmp_path)
        if record.get("outcome") == "cap_degraded"
    ]
    assert len(degraded) == 1, (
        "the 'no FIX-admission ledger' degradation MUST write a "
        f"'cap_degraded' run record — got {len(degraded)}"
    )
    assert degraded[0].get("degraded_reason") == "no_fix_admission_ledger"
    assert degraded[0].get("run_id") == "run-degraded-ledger"


@pytest.mark.asyncio
async def test_m18_the_missing_counter_degradation_is_always_recorded(
    tmp_path, monkeypatch
):
    """The PERMANENT "repository lacks the counter" degradation must record.

    Named mutation: remove the ``append_run_record`` from the
    "does not implement count_fix_admissions_since" branch → this pin goes
    RED.
    """
    dispatcher = SkillJobDispatcher(
        job_service=_LedgerlessJobService(),
        queue_repo=_LedgerlessQueueRepo(),
        evolution_lane_enabled=True,
        fix_cooldown_seconds=0,
    )
    # The ledger resolves to an object WITHOUT the count accessor — the
    # second (defensive) degradation branch.
    monkeypatch.setattr(
        dispatcher, "_fix_admission_ledger", lambda: object()
    )

    refusal = await dispatcher._check_fix_burst_cap(
        SKILL_A, run_id="run-degraded-counter"
    )
    assert refusal is None, (
        "the degraded posture stays FAIL-OPEN for availability — the "
        "admission proceeds"
    )

    degraded = [
        record
        for record in _sink_records(tmp_path)
        if record.get("outcome") == "cap_degraded"
    ]
    assert len(degraded) == 1, (
        "the 'missing count_fix_admissions_since' degradation MUST write a "
        f"'cap_degraded' run record — got {len(degraded)}"
    )
    assert (
        degraded[0].get("degraded_reason")
        == "missing_count_fix_admissions_since"
    )
    assert degraded[0].get("run_id") == "run-degraded-counter"


@pytest.mark.asyncio
async def test_m18_the_transient_degradation_keeps_its_own_outcome(tmp_path):
    """The transient branch keeps its distinct outcome (M18 must not blur them).

    Named mutation: rename the transient branch's outcome to
    "cap_degraded" → this pin goes RED (the two postures become
    indistinguishable).
    """
    repo = JobRepository(
        create_engine(
            f"sqlite:///{tmp_path / 'degraded.db'}",
            connect_args={"check_same_thread": False},
        )
    )
    SQLModel.metadata.create_all(repo.engine)

    class _RaisingLedger:
        def count_fix_admissions_since(self, skill_id, since_iso):
            raise RuntimeError("the ledger is down")

    dispatcher = SkillJobDispatcher(
        job_service=_FakeJobService(repo),
        queue_repo=_StubQueueRepo(),
        evolution_lane_enabled=True,
        fix_cooldown_seconds=0,
    )
    dispatcher._fix_admission_ledger = lambda: _RaisingLedger()  # type: ignore[method-assign]

    refusal = await dispatcher._check_fix_burst_cap(
        SKILL_A, run_id="run-cap-check-error"
    )
    assert refusal is None

    records = _sink_records(tmp_path)
    outcomes = [
        record.get("outcome")
        for record in records
        if record.get("stage") == "admission"
    ]
    assert "cap_check_error" in outcomes, (
        "the transient degradation must keep its own 'cap_check_error' "
        f"outcome — got {outcomes}"
    )
    assert "cap_degraded" not in outcomes, (
        "the transient degradation must NOT be blurred into 'cap_degraded' "
        f"— got {outcomes}"
    )
